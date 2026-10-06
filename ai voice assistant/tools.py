"""Tools for the local demo agent. Gmail credentials stay on the server."""
import asyncio
from email.message import EmailMessage
import json
import os
import smtplib
import ssl
import tempfile

from fastapi import HTTPException
from livekit.agents import function_tool
from livekit.agents.llm import ToolError
import auth_store


def normalize_email(value: str) -> str:
    """Accept one mailbox, not a recipient list or email header."""
    value = value.strip().lower()
    if len(value) > 254 or any(c in value for c in '\r\n,;<>'):
        raise ToolError('Provide one valid email address.')
    try:
        return auth_store.Credentials.normalize_email(value)
    except ValueError:
        raise ToolError('Provide one valid email address.') from None


def remove_block(email: str) -> dict:
    """Atomically replace the demo block list while preserving other entries."""
    email = normalize_email(email)
    with auth_store.LOCK:
        try:
            data = auth_store.read_json('blocked_ids.json')
        except HTTPException:
            raise ToolError('The blocked account file could not be read.') from None
        entries = data.get('blocked_emails') if isinstance(data, dict) else None
        if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
            raise ToolError('The blocked account file has an invalid format.')
        remaining = [item for item in entries if item.strip().lower() != email]
        if len(remaining) == len(entries):
            return {'status': 'not_blocked', 'email': email}
        data['blocked_emails'] = remaining
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=auth_store.DATA,
                                             suffix='.tmp', delete=False) as output:
                temporary = output.name
                json.dump(data, output, indent=2)
                output.write('\n')
            os.replace(temporary, auth_store.DATA / 'blocked_ids.json')
        except OSError:
            raise ToolError('Could not save the updated blocked account list.') from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return {'status': 'unblocked', 'email': email,
                'next_step': 'Try logging in with your existing password. Register if you do not have an account.'}


def deliver_summary(recipient: str, summary: str) -> None:
    """Send plain-text email over Gmail SSL; do not log SMTP credentials."""
    recipient = normalize_email(recipient)
    sender = os.getenv('GMAIL_EMAIL', '').strip()
    password = os.getenv('GMAIL_APP_PASSWORD', '').replace(' ', '').strip()
    if not sender or not password:
        raise ToolError('Summary email is not configured on the server.')
    sender = normalize_email(sender)
    if not summary.strip() or len(summary) > 12000:
        raise ToolError('Provide a non-empty summary of at most 12000 characters.')
    message = EmailMessage()
    message['From'] = sender
    message['To'] = recipient
    message['Subject'] = 'Your conversation summary with Alex'
    message.set_content('Hello,\n\nHere is your AI IT support conversation summary:\n\n'
                        + summary.strip() + '\n\n— Alex, AI IT support assistant\n')
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(sender, password)
            rejected = smtp.send_message(message)
            if rejected:
                raise ToolError('The mail server rejected the recipient.')
    except smtplib.SMTPAuthenticationError:
        raise ToolError('Gmail authentication failed. Ask the administrator to check email settings.') from None
    except (smtplib.SMTPException, OSError):
        # A timeout after submission can mean delivery succeeded; do not retry silently.
        raise ToolError('Email delivery could not be confirmed. Do not automatically retry; ask the user to check their inbox.') from None


def create_agent_tools():
    """Create two tools with isolated per-conversation email state."""
    email_attempted = False
    email_lock = asyncio.Lock()

    @function_tool(on_duplicate='ignore')
    async def unblock_user(email: str) -> dict:
        """Remove an email from the local DEMO block list at that user's request.

        Ask the user to confirm the exact email before calling. This is a demo
        recovery policy, not verification of account ownership. Does not change
        passwords or create an account. Never ask for the user's password.
        """
        return await asyncio.to_thread(remove_block, email)

    @function_tool(on_duplicate='ignore')
    async def send_conversation_summary(email: str, summary: str) -> dict:
        """Email the current conversation summary after the user requests it.

        First offer a summary, collect and confirm the recipient email and their
        consent to send. Summarize only this conversation: problem, steps taken,
        actual outcome, and remaining actions. Never include passwords, codes,
        API keys, or invent a resolution. Only one send attempt per conversation.
        """
        nonlocal email_attempted
        recipient = normalize_email(email)
        async with email_lock:
            if email_attempted:
                raise ToolError('A summary send was already attempted in this conversation. Check the inbox; no duplicate was sent.')
            email_attempted = True
            await asyncio.to_thread(deliver_summary, recipient, summary)
        return {'status': 'accepted_by_mail_server', 'email': recipient}

    return [unblock_user, send_conversation_summary]
