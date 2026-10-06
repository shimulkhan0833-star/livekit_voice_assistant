import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import auth_store
from tools import remove_block, deliver_summary, create_agent_tools
from livekit.agents.llm import ToolError


class ToolTests(unittest.TestCase):
    def test_unblock_preserves_other_ids(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(auth_store, 'DATA', Path(folder)):
            path = Path(folder) / 'blocked_ids.json'
            path.write_text(json.dumps({'blocked_emails': ['User@gmail.com', 'other@example.com']}))
            self.assertEqual(remove_block('user@gmail.com')['status'], 'unblocked')
            self.assertEqual(json.loads(path.read_text())['blocked_emails'], ['other@example.com'])
            self.assertEqual(remove_block('user@gmail.com')['status'], 'not_blocked')

    def test_invalid_json_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(auth_store, 'DATA', Path(folder)):
            path = Path(folder) / 'blocked_ids.json'
            path.write_text('invalid')
            with self.assertRaises(ToolError):
                remove_block('user@gmail.com')
            self.assertEqual(path.read_text(), 'invalid')

    def test_email_with_mock_smtp(self):
        with patch.dict(os.environ, {'GMAIL_EMAIL': 'sender@gmail.com', 'GMAIL_APP_PASSWORD': 'test-only'}), patch('tools.smtplib.SMTP_SSL') as smtp:
            connection = smtp.return_value.__enter__.return_value
            connection.send_message.return_value = {}
            deliver_summary('user@example.com', 'Printer fixed after reconnecting.')
            message = connection.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'user@example.com')
            self.assertIn('Printer fixed', message.get_content())
            self.assertEqual(smtp.call_args.args, ('smtp.gmail.com', 465))

    def test_header_injection_rejected(self):
        with self.assertRaises(ToolError):
            deliver_summary('user@example.com\nBcc: other@example.com', 'Summary')

    def test_two_tools_registered(self):
        self.assertEqual([tool.id for tool in create_agent_tools()], ['unblock_user', 'send_conversation_summary'])
