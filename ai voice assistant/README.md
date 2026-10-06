# AI Avatar IT Support Agent

## Agent tools

`tools.py` supplies two tools registered in `voice_assis.py`:

- `unblock_user`: after the user requests help and confirms their email, remove
  that email from `data/blocked_ids.json`. The change is saved immediately; the
  existing password is still needed to log in. No account is created. This dummy
  policy permits guest-requested unblocking and does not verify email ownership.
- `send_conversation_summary`: Alex offers a summary when wrapping up, asks for
  the recipient, confirms permission, and sends the problem/steps/outcome through
  Gmail SMTP using `GMAIL_EMAIL` and `GMAIL_APP_PASSWORD` from `.env`. Use a Gmail
  app password, not the normal account password. At most one send attempt is
  allowed per conversation to avoid duplicate messages after uncertain timeouts.

Restart the voice worker after changes. Say "My demo account is blocked" to try
recovery, or "Please email me a summary" to request the email flow. The agent must
collect the address before disconnection: clicking End conversation immediately
ends the call and cannot trigger a follow-up question. No email is sent automatically
after disconnection. SMTP acceptance does not guarantee inbox delivery.

Run `python -m unittest -v test_tools` for offline tests; these mock Gmail and
use temporary block lists. No live email is sent by the test suite.
Use one voice worker process for JSON block-list writes; a production version
needs verified recovery and a transactional shared store.

LiveKit Python backend with spoken IT troubleshooting and an optional Beyond Presence or Anam
avatar. LiveKit Inference handles transcription, reasoning, and speech.
The avatar provider publishes synchronized audio and video.

## Setup (PowerShell, Python 3.10+)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit an existing `.env` instead of overwriting it. Fill in LiveKit Cloud project
credentials and configure inference access. Model defaults retain the original
pipeline; select models available to your project. Groq and ElevenLabs keys
are not used. LiveKit and avatar usage may incur charges.

For Beyond Presence video, set `BEY_API_KEY` and `BEY_AVATAR_ID` in `.env`,
and set `AVATAR_PROVIDER=bey`. The avatar starts before the greeting and
publishes synchronized speech and video into the same LiveKit room.

For Anam video, get an API key and avatar ID from [Anam Lab](https://lab.anam.ai).
Fill in `ANAM_API_KEY` and `ANAM_AVATAR_ID`, and set `AVATAR_PROVIDER=anam`.

```powershell
.\.venv\Scripts\python voice_assis.py download-files
.\.venv\Scripts\python voice_assis.py dev
```

Use LiveKit's Agent Console with the same project, dispatch `my-agent` (or your
`AGENT_NAME`), and allow microphone access. Use its avatar preview to see video.
This script is a backend and does not open a local web page.

For terminal voice testing, set `AVATAR_PROVIDER=none` in `.env` and run:

```powershell
.\.venv\Scripts\python voice_assis.py console
```

Console mode does not show video. A custom frontend must join the same room,
dispatch the configured agent, and subscribe to the avatar worker's audio/video.
Keep all API secrets on the backend.

## FastAPI session API

### Browser demo

#### Demo accounts

Sign-in is optional: guests can click **Talk to Alex → Start conversation**.
Signed-in users are still checked against the blocked email list. An email block
cannot identify or prevent that person from returning anonymously as a guest.

Click **Sign in**, then **Create an account** the first time. Enter your email
(Gmail addresses work) and a separate demo password of at least 8 characters.
This is a local account, not Google OAuth; never enter your actual Gmail password.
Registration signs you in. You can sign out and log back in with those credentials.

- `data/users.json` stores normalized email addresses and salted scrypt password hashes.
- `data/blocked_ids.json` controls blocked email addresses:

```json
{"blocked_emails": ["blocked-demo@example.com", "person@gmail.com"]}
```

Edit this file to block/unblock accounts; no restart is required. Blocked accounts
cannot register, sign in, or start new demo conversations. This does not forcibly
terminate a call already in progress. Session cleanup remains available.
Files are backend-only and are not served under `/static`.
Do not delete the JSON files: an unavailable or malformed store denies access.

Cookies are HttpOnly and SameSite Strict (Secure on HTTPS), expire after 8 hours,
and are invalidated by logout. Login sessions are in memory, so restarting/reloading
the API signs everyone out; registered accounts persist in JSON. Run a single API
worker for this dummy system. It has no email verification, password reset, or
login rate limiting and is not intended for public production deployment.
The bearer-key `/sessions` endpoints remain reserved for trusted backend callers.

With the API and worker running, open **http://127.0.0.1:8000/**.
Click **Talk to Alex**, then **Start conversation**, and allow microphone access.
The floating chat panel displays the avatar and includes microphone, camera,
screen sharing, and end-call controls. Camera starts off; **Camera on** asks for
permission and displays a local preview. **Camera off** stops capture.
The agent reads screen shares only; publishing camera video does not enable camera understanding.
Minimizing keeps the call running; **End conversation** disconnects it.

The conversation area shows your spoken transcripts, Alex's responses, and typed
messages. Type and press Enter or **Send** while connected. You can mute your mic
and continue typing. Messages use LiveKit's `lk.chat` topic; transcripts arrive
on `lk.transcription`, with final text replacing interim text for the same segment.
History is kept in the page until the next call or refresh, not stored in a database.
After updating, restart the voice worker and refresh the browser to enable text output.

The HTML/CSS/JavaScript files are in `web/`. LiveKit JS (pinned to 2.22.3) loads
from jsDelivr; Google Fonts are optional and have system fallbacks. Internet
access is required for the SDK and LiveKit. Use a current desktop browser for screen sharing.
The `/demo/sessions` bridge accepts only loopback peers, a local Host, a matching
Origin when present, and a custom request header. It keeps `VOICE_API_KEY` on the
server. This is for direct localhost testing, not deployment behind a proxy:
disable the bridge or replace it with user authentication before hosting publicly.
The original `/sessions` endpoints still require bearer authentication.
Close-tab cleanup is best effort; use **End conversation** for reliable cleanup.

### API setup

Install `requirements.txt` in your active Python environment. Set `VOICE_API_KEY`
in `.env` to a random secret of at least 32 characters. Generate one with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.
Keep the existing LiveKit and Beyond Presence credentials.

Run these in two separate terminals in this directory:

```powershell
# Terminal 1: voice/avatar worker
python voice_assis.py dev
```

```powershell
# Terminal 2: HTTP API
python -m uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload
```
python -m uvicorn api_server:app --host 127.0.0.1 --port 8001 --reload

Open http://127.0.0.1:8000/docs. Click **Authorize** and paste the value of


`VOICE_API_KEY` (without a `Bearer` prefix). Try `POST /sessions` with:

```json
{"participant_name": "Shimul"}
```

The response contains `livekit_url`, `room_name`, `participant_identity`,
`participant_token` (valid for joining for 10 minutes), `session_token`, and
`agent_name`. A LiveKit client uses the URL and participant token to join and
publish microphone/screen tracks and subscribe to the avatar. Token expiration
does not end an already connected conversation. HTTP 201 means room creation
and dispatch succeeded; it does not confirm that the worker/avatar is ready.
Swagger tests HTTP requests, not microphone audio or avatar playback.

| Endpoint | Authentication | Result |
| --- | --- | --- |
| `GET /health` | None | API liveness; does not check the worker or upstream services |
| `POST /sessions` | `Authorization: Bearer <VOICE_API_KEY>` | Create a unique room and dispatch the configured agent |
| `DELETE /sessions/{room_name}` | Same bearer key plus `X-Session-Token: <session_token>` | Disconnect the session; 204 also when already deleted |

Save the session token returned by creation and use it to delete that same room.
It survives API restarts; rotating `VOICE_API_KEY` invalidates existing session
management tokens. Deletion cannot target arbitrary rooms or use another
session's token. If dispatch fails, the API attempts to remove the created room.
Failed cleanup may require deleting the room in LiveKit. Upstream failures return
502; invalid authentication returns 401 and a mismatched session token returns 403.

This first version uses a shared key for trusted backend callers/local testing,
with possession of a session token authorizing session deletion. It does not
provide individual user accounts. Do not embed the shared key in a public
frontend; add user authentication and rate limits before public deployment.
Use HTTPS outside localhost. No database or frontend is required for API testing.

Run offline tests (LiveKit calls are mocked):

```powershell
python -m unittest -v test_api_server
```

Implementation references: [LiveKit explicit dispatch](https://docs.livekit.io/agents/server/agent-dispatch/)
and [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/).

## Agent scope and verification

Alex provides step-by-step advice and handoff summaries. Device control,
company knowledge, ticket submission, and human transfers are not integrated.

- Ask for Wi-Fi help: expect one clarifying question and one step at a time.
- Interrupt an answer: verify that speech stops and the agent listens.
- Ask for a password reset: expect official recovery guidance, no secret collection.
- With an avatar enabled, verify synchronized video/audio with no duplicate speech.

References: [LiveKit Beyond Presence integration](https://docs.livekit.io/agents/models/avatar/plugins/bey/),
[LiveKit Anam integration](https://docs.livekit.io/agents/models/avatar/plugins/anam/)
and [avatar routing](https://docs.livekit.io/agents/models/avatar/).
