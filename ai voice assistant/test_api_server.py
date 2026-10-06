"""Offline HTTP contract tests: python -m unittest -v test_api_server."""
import os
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import jwt
from livekit import api
import api_server
import auth_store


class SessionTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        data = Path(temp)
        (data / 'users.json').write_text('{"users": {}}')
        (data / 'blocked_ids.json').write_text('{"blocked_emails": []}')
        self.enterContext(patch.object(auth_store, 'DATA', data))
        self.enterContext(patch.dict(auth_store.SESSIONS, {}, clear=True))
        self.env = patch.dict(os.environ, {
            'VOICE_API_KEY': 'test-api-key-' + 'x' * 32,
            'LIVEKIT_URL': 'wss://example.test', 'LIVEKIT_API_KEY': 'test-key',
            'LIVEKIT_API_SECRET': 'test-secret-' + 'x' * 32, 'AGENT_NAME': 'my-agent',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.remote = SimpleNamespace(
            room=SimpleNamespace(create_room=AsyncMock(), delete_room=AsyncMock()),
            agent_dispatch=SimpleNamespace(create_dispatch=AsyncMock()),
        )
        manager = AsyncMock()
        manager.__aenter__.return_value = self.remote
        self.factory = patch.object(api_server.api, 'LiveKitAPI', return_value=manager)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.client = self.enterContext(TestClient(api_server.app))
        self.headers = {'Authorization': 'Bearer ' + os.environ['VOICE_API_KEY']}

    def create(self):
        response = self.client.post('/sessions', json={'participant_name': 'Tester'}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_health_and_authentication(self):
        self.assertEqual(self.client.get('/health').json(), {'status': 'ok'})
        for headers in ({}, {'Authorization': 'Bearer invalid'}):
            self.assertEqual(self.client.post('/sessions', json={}, headers=headers).status_code, 401)
        self.remote.room.create_room.assert_not_awaited()

    def test_create_scoped_token_and_dispatch(self):
        response = self.create()
        data = response.json()
        claims = jwt.decode(data['participant_token'], os.environ['LIVEKIT_API_SECRET'], algorithms=['HS256'])
        self.assertEqual(claims['video']['room'], data['room_name'])
        self.assertTrue(claims['video']['roomJoin'])
        self.assertFalse(claims['video'].get('roomAdmin', False))
        self.assertEqual(claims['sub'], data['participant_identity'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        dispatch = self.remote.agent_dispatch.create_dispatch.call_args.args[0]
        self.assertEqual(dispatch.agent_name, 'my-agent')
        self.assertEqual(dispatch.room, data['room_name'])

    def test_delete_requires_matching_session_secret(self):
        first, second = self.create().json(), self.create().json()
        url = '/sessions/' + first['room_name']
        wrong = {**self.headers, 'X-Session-Token': second['session_token']}
        self.assertEqual(self.client.delete(url, headers=wrong).status_code, 403)
        self.remote.room.delete_room.assert_not_awaited()
        right = {**self.headers, 'X-Session-Token': first['session_token']}
        self.assertEqual(self.client.delete(url, headers=right).status_code, 204)
        self.remote.room.delete_room.assert_awaited_once()

    def test_dispatch_failure_cleans_room(self):
        self.remote.agent_dispatch.create_dispatch.side_effect = TimeoutError()
        response = self.client.post('/sessions', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.remote.room.delete_room.assert_awaited_once()

    def test_delete_not_found_is_idempotent(self):
        data = self.create().json()
        self.remote.room.delete_room.side_effect = api.TwirpError('not_found', 'gone', status=404)
        response = self.client.delete('/sessions/' + data['room_name'], headers={
            **self.headers, 'X-Session-Token': data['session_token'],
        })
        self.assertEqual(response.status_code, 204)

    def test_input_validation(self):
        for body in ({'participant_name': ' '}, {'room_name': 'arbitrary-room'}):
            self.assertEqual(self.client.post('/sessions', json=body, headers=self.headers).status_code, 422)
        self.remote.room.create_room.assert_not_awaited()

    def test_demo_page_and_local_bridge(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/static/app.js').status_code, 200)
        # Default TestClient peer is not loopback: bridge rejects it.
        self.assertEqual(self.client.post('/demo/sessions', json={}, headers={'X-Demo-Client': 'voice-demo'}).status_code, 403)
        with TestClient(api_server.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50000)) as local:
            self.assertEqual(local.post('/demo/sessions', json={}).status_code, 403)
            headers = {'X-Demo-Client': 'voice-demo', 'Origin': 'http://evil.example'}
            self.assertEqual(local.post('/demo/sessions', json={}, headers=headers).status_code, 403)
            headers['Origin'] = 'http://127.0.0.1:8000'
            self.assertEqual(local.post('/demo/sessions', json={}, headers=headers).status_code, 201)
            local.cookies.set(auth_store.COOKIE, 'expired-session')
            self.assertEqual(local.post('/demo/sessions', json={}, headers=headers).status_code, 201)
            local.cookies.clear()
            self.assertEqual(local.post('/auth/register', json={'email': 'demo@example.com', 'password': 'test-password-123'}, headers=headers).status_code, 201)
            response = local.post('/demo/sessions', json={}, headers=headers)
            self.assertEqual(response.status_code, 201)
            data = response.json()
            headers['X-Session-Token'] = data['session_token']
            self.assertEqual(local.delete('/demo/sessions/' + data['room_name'], headers=headers).status_code, 204)

    def test_login_blocking_and_password_storage(self):
        with TestClient(api_server.app, base_url='http://127.0.0.1:8000', client=('127.0.0.1', 50001)) as client:
            headers = {'X-Demo-Client': 'voice-demo'}
            credentials = {'email': 'USER@gmail.com', 'password': 'demo-password-123'}
            self.assertEqual(client.post('/auth/register', json=credentials, headers=headers).status_code, 201)
            stored = (auth_store.DATA / 'users.json').read_text()
            self.assertNotIn(credentials['password'], stored)
            self.assertIn('password_hash', stored)
            self.assertEqual(client.post('/auth/register', json=credentials, headers=headers).status_code, 409)
            client.post('/auth/logout', headers=headers)
            self.assertEqual(client.get('/auth/me', headers=headers).status_code, 401)
            self.assertEqual(client.post('/auth/login', json={**credentials, 'password': 'wrong-password'}, headers=headers).status_code, 401)
            self.assertEqual(client.post('/auth/login', json=credentials, headers=headers).status_code, 200)
            self.assertEqual(client.get('/auth/me', headers=headers).json()['email'], 'user@gmail.com')
            (auth_store.DATA / 'blocked_ids.json').write_text(json.dumps({'blocked_emails': ['user@gmail.com']}))
            self.assertEqual(client.post('/auth/login', json=credentials, headers=headers).status_code, 403)
            self.assertEqual(client.get('/auth/me', headers=headers).status_code, 403)
            self.assertEqual(client.post('/demo/sessions', json={}, headers=headers).status_code, 403)


if __name__ == '__main__':
    unittest.main()
