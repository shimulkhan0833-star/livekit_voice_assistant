import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from livekit import rtc
from livekit.agents.llm import ChatMessage, ImageContent
from voice_assis import Assistant


class ScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_and_text_receive_recent_frame(self):
        assistant = Assistant()
        assistant._latest_frame = rtc.VideoFrame(2, 2, rtc.VideoBufferType.RGBA, bytes(16))
        assistant._frame_received_at = time.monotonic()
        message = ChatMessage(role='user', content=['What is on screen?'])
        await assistant.on_user_turn_completed(None, message)
        self.assertIsInstance(message.content[-1], ImageContent)
        session = SimpleNamespace(interrupt=AsyncMock(), generate_reply=Mock())
        await assistant.on_text_input(session, SimpleNamespace(text='And now?'))
        sent = session.generate_reply.call_args.kwargs['user_input']
        self.assertIsInstance(sent.content[-1], ImageContent)
        self.assertEqual(sent.content[0], 'And now?')

    async def test_old_and_muted_frames_not_used(self):
        assistant = Assistant()
        assistant._latest_frame = object()
        assistant._frame_received_at = time.monotonic() - 20
        self.assertIsNone(await assistant.screen_image())
        assistant._screen_sid = 'screen'
        assistant._frame_ready.set()
        assistant._on_track_muted(SimpleNamespace(sid='screen'), None)
        self.assertIsNone(assistant._latest_frame)
        self.assertFalse(assistant._frame_ready.is_set())
