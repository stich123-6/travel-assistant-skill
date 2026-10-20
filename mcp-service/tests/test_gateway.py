import tempfile
import unittest
import time
from unittest.mock import patch
from pathlib import Path
from cryptography.fernet import Fernet

from travel_mcp.config import Settings
from travel_mcp.store import ServiceError, Store
from travel_mcp.workflow import TravelService
from travel_mcp.upstream import Gateway, safe_result
from travel_mcp.actions import Actions


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.timeout = False

    async def request(self, url, tool=None, arguments=None):
        self.calls.append((url, tool, arguments))
        if tool is None:
            return [{'name': name, 'inputSchema': {'type': 'object'}} for name in (
                'maps_textsearch', 'taxi_estimate', 'taxi_create_order', 'directionDriving')]
        if self.timeout:
            raise TimeoutError('private upstream url')
        if tool == 'maps_textsearch':
            return {'structured': {'pois': [{'name': 'A', 'location': '121.1,31.1'}, {'name': 'B', 'location': '121.2,31.2'}]}, 'is_error': False}
        if tool == 'taxi_estimate':
            return {'structured': {'traceId': 'trace-real', 'items': [{'productCategory': 'fast', 'productName': '快车', 'priceText': '约30元'}]}, 'is_error': False}
        return {'structured': {'orderId': 'order-real'}, 'is_error': False}


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = Store(Path(self.tmp.name), Fernet.generate_key().decode())
        self.uid = self.store.create_user('jane', 'test-password-only')['id']
        self.fake = FakeTransport()
        self.settings = Settings(public_base_url='http://127.0.0.1:8030', data_dir=Path(self.tmp.name), encryption_key=Fernet.generate_key().decode(), allow_insecure_localhost=True)
        self.gateway = Gateway(self.store, self.settings, self.fake)
        self.actions = Actions(self.store, self.gateway)
        self.trip = TravelService(self.store).intake(self.uid, '本市出行', {
            'origin_address': 'A地址', 'destination_address': 'B地址', 'departure_at': '2026-10-21T09:00:00+08:00'})

    def tearDown(self):
        self.tmp.cleanup()

    async def test_missing_credentials_and_unverified_provider_do_not_call(self):
        for provider in ('didi', 'tongcheng', 'dingtalk'):
            result = await self.gateway.tools(self.uid, provider)
            self.assertEqual(result['status'], 'pending_integration')
        self.assertEqual(self.fake.calls, [])

    async def test_only_fixed_official_endpoint_and_allowlisted_read_tools(self):
        self.store.set_credentials(self.uid, 'didi', {'key': 'test-key-not-real'})
        tools = await self.gateway.tools(self.uid, 'didi')
        self.assertNotIn('taxi_create_order', [x['name'] for x in tools['tools']])
        with self.assertRaises(ServiceError):
            await self.gateway.query(self.uid, self.trip['id'], self.trip['revision'], 'didi', 'taxi_create_order', {})
        result = await self.gateway.query(self.uid, self.trip['id'], self.trip['revision'], 'didi', 'maps_textsearch', {'city': '上海市', 'keywords': 'A'})
        self.assertTrue(self.fake.calls[-1][0].startswith('https://mcp.didichuxing.com/mcp-servers?key='))
        self.assertEqual(result['receipt']['tool'], 'maps_textsearch')
        self.assertNotIn('test-key-not-real', str(result))

    async def test_exhibition_gate_prevents_speculative_map_calls(self):
        self.store.set_credentials(self.uid, 'tencentmap', {'key': 'test-map-key'})
        exhibition = TravelService(self.store).intake(self.uid, '去上海参加农业展会', {'city': '上海市'})
        with self.assertRaises(ServiceError) as caught:
            await self.gateway.query(self.uid, exhibition['id'], exhibition['revision'], 'tencentmap', 'directionDriving', {})
        self.assertEqual(caught.exception.code, 'needs_input')
        self.assertEqual(self.fake.calls, [])

    async def estimate(self):
        self.store.set_credentials(self.uid, 'didi', {'key': 'test-key-not-real'})
        located = await self.gateway.query(self.uid, self.trip['id'], self.trip['revision'], 'didi', 'maps_textsearch', {'city': '上海市', 'keywords': 'A B'})
        args = {'from_lat': '31.1', 'from_lng': '121.1', 'from_name': 'A', 'to_lat': '31.2', 'to_lng': '121.2', 'to_name': 'B'}
        return await self.gateway.query(self.uid, self.trip['id'], located['record']['revision'], 'didi', 'taxi_estimate', args)

    async def test_estimate_coordinates_must_come_from_real_lookup(self):
        self.store.set_credentials(self.uid, 'didi', {'key': 'test-key-not-real'})
        with self.assertRaises(ServiceError):
            await self.gateway.query(self.uid, self.trip['id'], self.trip['revision'], 'didi', 'taxi_estimate', {'from_lat': '31.1', 'from_lng': '121.1', 'to_lat': '31.2', 'to_lng': '121.2'})
        self.assertEqual(self.fake.calls, [])

    async def test_scoped_order_confirmation_and_timeout_never_retries(self):
        estimated = await self.estimate()
        proposal = self.actions.prepare_taxi(self.uid, self.trip['id'], estimated['record']['revision'], estimated['receipt']['id'], 'fast')
        self.assertEqual(proposal['status'], 'needs_confirmation')
        self.assertIn('约30元', str(proposal))
        with self.assertRaises(ServiceError):
            await self.actions.execute_taxi(self.uid, proposal['id'], proposal['revision'], '嗯')
        self.fake.timeout = True
        unknown = await self.actions.execute_taxi(self.uid, proposal['id'], proposal['revision'], '确认按此方案叫车')
        self.assertEqual(unknown['status'], 'unknown')
        before = len(self.fake.calls)
        again = await self.actions.execute_taxi(self.uid, proposal['id'], unknown['revision'], '确认按此方案叫车')
        self.assertEqual(again['status'], 'unknown')
        self.assertEqual(before, len(self.fake.calls))
        self.assertEqual(sum(x[1] == 'taxi_create_order' for x in self.fake.calls), 1)

    async def test_changed_trip_or_other_user_cannot_execute_old_quote(self):
        estimated = await self.estimate()
        proposal = self.actions.prepare_taxi(self.uid, self.trip['id'], estimated['record']['revision'], estimated['receipt']['id'], 'fast')
        other = self.store.create_user('other', 'test-password-only')['id']
        with self.assertRaises(ServiceError):
            await self.actions.execute_taxi(other, proposal['id'], 1, '确认按此方案叫车')
        TravelService(self.store).update(self.uid, self.trip['id'], {'origin_address': 'C'}, estimated['record']['revision'])
        with self.assertRaises(ServiceError):
            await self.actions.execute_taxi(self.uid, proposal['id'], 1, '确认按此方案叫车')
        self.assertFalse(any(x[1] == 'taxi_create_order' for x in self.fake.calls))

    async def test_same_quote_reprepare_cannot_bypass_unknown_even_after_restart(self):
        estimated = await self.estimate()
        first = self.actions.prepare_taxi(self.uid, self.trip['id'], estimated['record']['revision'], estimated['receipt']['id'], 'fast')
        self.fake.timeout = True
        unknown = await self.actions.execute_taxi(self.uid, first['id'], 1, '确认按此方案叫车')
        restarted = Actions(self.store, self.gateway)
        second = restarted.prepare_taxi(self.uid, self.trip['id'], estimated['record']['revision'], estimated['receipt']['id'], 'fast')
        self.assertEqual(second['id'], unknown['id'])
        await restarted.execute_taxi(self.uid, second['id'], second['revision'], '确认按此方案叫车')
        self.assertEqual(sum(x[1] == 'taxi_create_order' for x in self.fake.calls), 1)

    async def test_prepare_does_not_extend_real_estimate_lifetime(self):
        estimated = await self.estimate()
        received = estimated['receipt']['received_at']
        with patch('travel_mcp.actions.time.time', return_value=received + 599):
            proposal = self.actions.prepare_taxi(self.uid, self.trip['id'], estimated['record']['revision'], estimated['receipt']['id'], 'fast')
        self.assertEqual(proposal['expires_at'], received + 600)
        with patch('travel_mcp.actions.time.time', return_value=received + 601):
            with self.assertRaises(ServiceError):
                await self.actions.execute_taxi(self.uid, proposal['id'], proposal['revision'], '确认按此方案叫车')
        self.assertFalse(any(x[1] == 'taxi_create_order' for x in self.fake.calls))

    def test_errors_and_payloads_redact_encoded_keys(self):
        clean = safe_result({'url': 'https://x?key=abc%2Bsecret', 'value': 'abc+secret'}, ['abc+secret'])
        self.assertNotIn('abc', str(clean))

    def test_production_requires_https_and_stable_master_key(self):
        with self.assertRaises(ValueError):
            Settings(public_base_url='http://example.com', data_dir=Path(self.tmp.name), encryption_key=self.settings.encryption_key)
        with self.assertRaises(ValueError):
            Settings(public_base_url='https://example.com/prefix', data_dir=Path(self.tmp.name), encryption_key=self.settings.encryption_key)


if __name__ == '__main__':
    unittest.main()
