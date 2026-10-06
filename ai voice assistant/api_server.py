"""Serve the website, manage LiveKit rooms, and handle optional demo accounts.

Run this API with Uvicorn and run voice_assis.py separately. This file does not
process speech or create avatar video: LiveKit dispatches those jobs to the
voice worker using the matching AGENT_NAME.

Route groups:
  / and /static/...  - HTML, JavaScript, and CSS for the website.
  /sessions         - session management for callers with VOICE_API_KEY.
  /demo/sessions    - localhost website access, including anonymous guests.
  /auth/...         - optional email/password registration and login.
  /health           - check that the HTTP API is responding.
"""
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import hmac
import logging
import os
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import aiohttp
import auth_store
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Path as ApiPath, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from urllib.parse import urlsplit
from livekit import api
from pydantic import BaseModel, ConfigDict, Field

# Read configuration beside this file, regardless of the terminal's directory.
# Existing environment variables take precedence over values in .env.
load_dotenv(Path(__file__).with_name('.env'))
logger = logging.getLogger(__name__)
# Extract Authorization: Bearer <key>; authenticate() handles missing/bad keys.
bearer = HTTPBearer(auto_error=False)
TOKEN_TTL_SECONDS = 600  # Ten minutes to join; this does not limit call duration.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate settings and open one shared LiveKit client during API startup."""
    required = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'VOICE_API_KEY')
    settings = {key: os.getenv(key, '').strip() for key in required}
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise RuntimeError('Missing configuration: ' + ', '.join(missing))
    if len(settings['VOICE_API_KEY']) < 32:
        raise RuntimeError('VOICE_API_KEY must contain at least 32 characters.')
    # app.state lets route handlers access shared settings via request.app.
    app.state.settings = settings
    app.state.agent_name = os.getenv('AGENT_NAME', 'my-agent').strip()
    if not app.state.agent_name:
        raise RuntimeError('AGENT_NAME must not be empty.')
    # This client performs server-side room/dispatch requests, not media calls.
    # Its context manager closes network resources when the API shuts down.
    async with api.LiveKitAPI(
        url=settings['LIVEKIT_URL'], api_key=settings['LIVEKIT_API_KEY'],
        api_secret=settings['LIVEKIT_API_SECRET'],
        timeout=aiohttp.ClientTimeout(total=20),
    ) as livekit:
        app.state.livekit = livekit
        yield  # FastAPI serves requests until shutdown resumes this function.


# FastAPI also generates interactive API documentation at /docs.
app = FastAPI(title='Voice Agent API', version='1.0.0', lifespan=lifespan)
WEB_DIR = Path(__file__).with_name('web')
# Only web/ is public: .env and account JSON files are outside this directory.
app.mount('/static', StaticFiles(directory=WEB_DIR), name='static')


@app.get('/', include_in_schema=False)
async def demo_page():
    """GET /: show the landing page and floating voice/chat assistant."""
    return FileResponse(WEB_DIR / 'index.html')


def local_demo_only(request: Request):
    """Local demo bridge; never expose shared server credentials to JavaScript."""
    # Depends(local_demo_only) runs this check before the associated route.
    # Require a loopback peer/Host and the custom header sent by web/app.js.
    # This is a local-demo guard, not user authentication or proxy-safe access.
    local_hosts = {'localhost', '127.0.0.1', '::1'}
    if (not request.client or request.client.host not in local_hosts
            or request.url.hostname not in local_hosts
            or request.headers.get('x-demo-client') != 'voice-demo'):
        raise HTTPException(403, 'Demo sessions are available only on localhost.')
    # Reject browser requests originating from a different host or port.
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, 'Cross-origin demo requests are not allowed.')


def authenticate(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    """Protect /sessions with the backend API key, not a user's login cookie."""
    expected = request.app.state.settings['VOICE_API_KEY']
    if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(401, 'Invalid API key', headers={'WWW-Authenticate': 'Bearer'})


def session_secret(request: Request, room_name: str) -> str:
    """Create the room-specific deletion token without keeping a room database.

    The same room/key pair produces the same token across restarts. Knowing a
    room name alone is insufficient to end it; rotating the API key changes it.
    This token is separate from both the LiveKit join token and login cookie.
    """
    return hmac.new(
        request.app.state.settings['VOICE_API_KEY'].encode(),
        ('end-session:' + room_name).encode(), hashlib.sha256,
    ).hexdigest()


class SessionRequest(BaseModel):
    """JSON request body; participant_name is a display label, not a login ID."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    participant_name: str = Field(default='Guest', min_length=1, max_length=80)


class SessionResponse(BaseModel):
    """Connection details returned to the caller after room creation."""
    room_name: str
    livekit_url: str
    participant_identity: str
    participant_token: str  # Client passes this JWT to LiveKit to join the room.
    token_expires_in: int
    session_token: str  # Send in X-Session-Token when ending this room.
    agent_name: str


@app.get('/health')
async def health():
    """API liveness only; does not check worker availability or provider credentials."""
    return {'status': 'ok'}


@app.post('/sessions', response_model=SessionResponse, status_code=201,
          dependencies=[Depends(authenticate)])
async def create_session(body: SessionRequest, request: Request, response: Response):
    """Create a room and request agent dispatch. Does not wait for avatar readiness."""
    # POST /sessions requires bearer auth. demo_create also reuses this function.
    settings = request.app.state.settings
    # Generate separate room and participant IDs for every conversation.
    room_name = 'voice-' + uuid4().hex
    identity = 'user-' + uuid4().hex
    # Grant media/data access only to this room, without room-admin privileges.
    token = (api.AccessToken(settings['LIVEKIT_API_KEY'], settings['LIVEKIT_API_SECRET'])
             .with_identity(identity).with_name(body.participant_name)
             .with_ttl(timedelta(seconds=TOKEN_TTL_SECONDS))
             .with_grants(api.VideoGrants(room_join=True, room=room_name,
                                         can_publish=True, can_subscribe=True,
                                         can_publish_data=True)).to_jwt())
    livekit = request.app.state.livekit
    try:
        await livekit.room.create_room(api.CreateRoomRequest(
            name=room_name, empty_timeout=120, departure_timeout=20,
        ))
        # Ask LiveKit to assign the room to the registered voice_assis.py worker.
        # This is the connection between the two files; no Python import is needed.
        await livekit.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            room=room_name, agent_name=request.app.state.agent_name,
        ))
    except (api.TwirpError, aiohttp.ClientError, TimeoutError):
        # Also try cleanup after an ambiguous create timeout: the room may exist.
        try:
            await livekit.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except (api.TwirpError, aiohttp.ClientError, TimeoutError):
            logger.warning('Could not clean up failed session %s', room_name)
        raise HTTPException(502, 'LiveKit could not create the session. Please retry.') from None
    # The response includes sensitive tokens, so browsers must not cache it.
    response.headers['Cache-Control'] = 'no-store'
    return SessionResponse(
        room_name=room_name, livekit_url=settings['LIVEKIT_URL'],
        participant_identity=identity, participant_token=token,
        token_expires_in=TOKEN_TTL_SECONDS,
        session_token=session_secret(request, room_name),
        agent_name=request.app.state.agent_name,
    )


@app.delete('/sessions/{room_name}', status_code=204, dependencies=[Depends(authenticate)])
async def end_session(
    request: Request,
    room_name: Annotated[str, ApiPath(pattern=r'^voice-[0-9a-f]{32}$')],
    x_session_token: Annotated[str, Header(description='session_token returned by POST /sessions')],
):
    """Disconnect every participant. Requires the secret issued for this session."""
    # DELETE /sessions/{room_name}: verify the deletion token before contacting
    # LiveKit. The path pattern also restricts names to rooms this API creates.
    if not hmac.compare_digest(x_session_token.encode(), session_secret(request, room_name).encode()):
        raise HTTPException(403, 'Invalid session token')
    try:
        await request.app.state.livekit.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except api.TwirpError as exc:
        # Ending an already deleted room is successful too (safe to retry).
        if exc.code != 'not_found':
            raise HTTPException(502, 'LiveKit could not end the session. Please retry.') from None
    except (aiohttp.ClientError, TimeoutError):
        raise HTTPException(502, 'LiveKit could not end the session. Please retry.') from None
    return Response(status_code=204)


@app.post('/demo/sessions', response_model=SessionResponse, status_code=201,
          dependencies=[Depends(local_demo_only)], include_in_schema=False)
async def demo_create(body: SessionRequest, request: Request, response: Response):
    """POST /demo/sessions: start a website call without exposing VOICE_API_KEY."""
    # Login is optional. A valid logged-in account is checked against the block
    # list; an anonymous guest cannot be identified by an email block.
    # Expired logins become guests; blocked accounts remain denied.
    if request.cookies.get(auth_store.COOKIE):
        try:
            auth_store.current_user(request)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            response.delete_cookie(auth_store.COOKIE)
    # Direct function calls do not run FastAPI route dependencies again.
    # The localhost guard above intentionally replaces bearer auth here.
    return await create_session(body, request, response)


@app.delete('/demo/sessions/{room_name}', status_code=204,
            dependencies=[Depends(local_demo_only)], include_in_schema=False)
async def demo_end(
    request: Request,
    room_name: Annotated[str, ApiPath(pattern=r'^voice-[0-9a-f]{32}$')],
    x_session_token: Annotated[str, Header()],
):
    """DELETE /demo/sessions/{room_name}: end a website call using its token."""
    # Allow cleanup even after logout or blocking. The room-specific token and
    # localhost guard are still required, including for guest conversations.
    return await end_session(request, room_name, x_session_token)


def set_login_cookie(response: Response, request: Request, token: str):
    """Give the browser its login session token, never the password or API key."""
    # HttpOnly prevents JavaScript reading the cookie. SameSite restricts its
    # cross-site use. Secure is enabled on HTTPS; local HTTP demos still work.
    response.set_cookie(auth_store.COOKIE, token, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', max_age=auth_store.TTL)
    response.headers['Cache-Control'] = 'no-store'


@app.post('/auth/register', status_code=201, dependencies=[Depends(local_demo_only)])
def register_user(body: auth_store.Credentials, request: Request, response: Response):
    """POST /auth/register: save a new account and immediately sign it in."""
    # auth_store validates blocks/duplicates and writes a salted password hash
    # to data/users.json. This is a demo account, not Google/Gmail OAuth.
    auth_store.register(body)
    set_login_cookie(response, request, auth_store.issue_session(body.email))
    return {'email': body.email}


@app.post('/auth/login', dependencies=[Depends(local_demo_only)])
def login_user(body: auth_store.Credentials, request: Request, response: Response):
    """POST /auth/login: verify the stored password hash and issue a cookie."""
    # auth_store.login rejects blocked accounts and incorrect credentials.
    set_login_cookie(response, request, auth_store.login(body))
    return {'email': body.email}


@app.get('/auth/me', dependencies=[Depends(local_demo_only)])
def who_am_i(request: Request, response: Response):
    """GET /auth/me: let the website restore the signed-in email after refresh."""
    # Returns 401 for missing/expired login and 403 for a blocked account.
    response.headers['Cache-Control'] = 'no-store'
    return {'email': auth_store.current_user(request)}


@app.post('/auth/logout', dependencies=[Depends(local_demo_only)])
def logout_user(request: Request, response: Response):
    """POST /auth/logout: invalidate the server session and clear its cookie."""
    # Login sessions are in memory; accounts remain in JSON after logout.
    # The lock coordinates access with the other synchronous auth handlers.
    with auth_store.LOCK:
        auth_store.SESSIONS.pop(request.cookies.get(auth_store.COOKIE, ''), None)
    response.delete_cookie(auth_store.COOKIE)
    return {'status': 'signed out'}
