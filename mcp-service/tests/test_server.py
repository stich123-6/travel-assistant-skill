import tempfile
import unittest
import asyncio
import socket
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthClientInformationFull
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx2
import uvicorn
from travel_mcp.config import Settings
from travel_mcp.server import create_app


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_client_over_real_local_http(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            settings = Settings(f'http://127.0.0.1:{port}', Path(folder), Fernet.generate_key().decode(), allow_insecure_localhost=True)
            app = create_app(settings)
            uid = app.state.store.create_user('sdk-user', 'test-password-only')['id']
            with app.state.store.connection() as conn:
                token = app.state.oauth_provider._issue(conn, 'sdk-test', uid, ['travel'], settings.public_base_url + '/mcp')
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='critical', access_log=False))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                for _ in range(200):
                    if server.started:
                        break
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
                self.assertTrue(server.started)
                async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + token.access_token}, follow_redirects=False) as http:
                    async with streamable_http_client(settings.public_base_url + '/mcp', http_client=http) as streams:
                        async with ClientSession(streams[0], streams[1]) as session:
                            initialized = await session.initialize()
                            self.assertEqual(initialized.server_info.name, 'soco-travel-assistant')
                            tools = await session.list_tools()
                            self.assertEqual(len(tools.tools), 11)
                            result = await session.call_tool('travel_intake', {'message': '去上海参加一个农业展会', 'fields': {'city': '上海市'}})
                            self.assertFalse(result.is_error)
                            self.assertIn('你要参加的展会叫什么名字', str(result))
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 5)
                listener.close()

    async def test_authorized_mcp_protocol_and_user_scoped_memory(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            settings = Settings('https://travel.example', Path(folder), Fernet.generate_key().decode())
            app = create_app(settings)
            user = app.state.store.create_user('jane', 'test-password-only')
            other = app.state.store.create_user('other', 'test-password-only')
            provider = app.state.oauth_provider
            await provider.register_client(OAuthClientInformationFull(client_id='test-client',
                redirect_uris=['http://127.0.0.1:5000/oauth/callback'], token_endpoint_auth_method='none', scope='travel'))
            with app.state.store.connection() as conn:
                token = provider._issue(conn, 'test-client', user['id'], ['travel'], settings.public_base_url + '/mcp')
                other_token = provider._issue(conn, 'test-client', other['id'], ['travel'], settings.public_base_url + '/mcp')
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_base_url) as client:
                    health = await client.get('/health')
                    self.assertEqual(health.status_code, 200)
                    denied = await client.post('/mcp', json={})
                    self.assertEqual(denied.status_code, 401)
                    headers = {'Authorization': 'Bearer ' + token.access_token,
                               'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}

                    async def rpc(method, params, bearer=headers):
                        response = await client.post('/mcp', headers=bearer,
                            json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
                        self.assertEqual(response.status_code, 200, response.text)
                        result = response.json()
                        self.assertNotIn('error', result, result)
                        return result['result']

                    initialized = await rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
                    self.assertEqual(initialized['serverInfo']['name'], 'soco-travel-assistant')
                    listed = await rpc('tools/list', {})
                    names = [item['name'] for item in listed['tools']]
                    self.assertIn('travel_intake', names)
                    self.assertNotIn('set_credentials', names)
                    added = await rpc('tools/call', {'name': 'origin_memory', 'arguments': {'action': 'add', 'label': '家', 'address': '测试地址'}})
                    self.assertFalse(added.get('isError', False), added)
                    new_task = await rpc('tools/call', {'name': 'origin_memory', 'arguments': {'action': 'list'}})
                    self.assertIn('测试地址', str(new_task))
                    different = await rpc('tools/call', {'name': 'origin_memory', 'arguments': {'action': 'list'}},
                        {**headers, 'Authorization': 'Bearer ' + other_token.access_token})
                    self.assertNotIn('测试地址', str(different))
                    exhibition = await rpc('tools/call', {'name': 'travel_intake', 'arguments': {'message': '明天去上海参加农业展会', 'fields': {'city': '上海市'}}})
                    self.assertIn('你要参加的展会叫什么名字', str(exhibition))


if __name__ == '__main__':
    unittest.main()
