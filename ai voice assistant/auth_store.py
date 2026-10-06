"""Local demo accounts: JSON persistence, hashed passwords, expiring sessions."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator

DATA = Path(__file__).with_name('data')
LOCK = threading.RLock()
SESSIONS = {}
COOKIE = 'alex_session'
TTL = 8 * 60 * 60


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if value.count('@') != 1 or any(c.isspace() for c in value):
            raise ValueError('Enter a valid email address')
        local, domain = value.split('@')
        if not local or '.' not in domain or domain.startswith('.') or domain.endswith('.'):
            raise ValueError('Enter a valid email address')
        return value


def read_json(name):
    try:
        return json.loads((DATA / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(503, 'Account storage is unavailable. Check the JSON files.') from None


def check_blocked(email):
    data = read_json('blocked_ids.json')
    blocked = data.get('blocked_emails') if isinstance(data, dict) else None
    if not isinstance(blocked, list) or not all(isinstance(item, str) for item in blocked):
        raise HTTPException(503, 'Invalid blocked_ids.json format')
    if email in {item.strip().lower() for item in blocked}:
        raise HTTPException(403, 'This account is blocked.')


def users():
    data = read_json('users.json')
    if not isinstance(data, dict) or not isinstance(data.get('users'), dict):
        raise HTTPException(503, 'Invalid users.json format')
    return data


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def register(credentials):
    with LOCK:
        check_blocked(credentials.email)
        data = users()
        if credentials.email in data['users']:
            raise HTTPException(409, 'An account with this email already exists. Please sign in.')
        salt = secrets.token_hex(16)
        data['users'][credentials.email] = {
            'salt': salt, 'password_hash': password_hash(credentials.password, salt),
            'algorithm': 'scrypt-n16384-r8-p1',
        }
        temp = DATA / 'users.json.tmp'
        try:
            temp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
            os.replace(temp, DATA / 'users.json')
        except OSError:
            raise HTTPException(503, 'Could not save the account.') from None


def login(credentials):
    with LOCK:
        check_blocked(credentials.email)
        user = users()['users'].get(credentials.email)
        salt = user['salt'] if user else '00' * 16
        calculated = password_hash(credentials.password, salt)
        if not user or not hmac.compare_digest(calculated, user['password_hash']):
            raise HTTPException(401, 'Incorrect email or password.')
        return issue_session(credentials.email)


def issue_session(email):
    with LOCK:
        now = time.time()
        for key in list(SESSIONS):
            if SESSIONS[key][1] <= now:
                del SESSIONS[key]
        token = secrets.token_urlsafe(32)
        SESSIONS[token] = (email, now + TTL)
        return token


def current_user(request: Request):
    with LOCK:
        token = request.cookies.get(COOKIE, '')
        record = SESSIONS.get(token)
        if not record or record[1] <= time.time():
            SESSIONS.pop(token, None)
            raise HTTPException(401, 'Please sign in to continue.')
        check_blocked(record[0])
        if record[0] not in users()['users']:
            raise HTTPException(401, 'Account no longer exists.')
        return record[0]
