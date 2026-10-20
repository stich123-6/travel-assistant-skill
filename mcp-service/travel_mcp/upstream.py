"""Official MCP endpoints, real schema discovery and user-scoped read receipts."""

import json
import logging
import math
import time
import uuid
from urllib.parse import quote, quote_plus

import httpx2
from jsonschema import Draft202012Validator, ValidationError, SchemaError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .store import ServiceError


READ_TOOLS = {
    'tencentmap': frozenset({'geocoder', 'reverseGeocoder', 'placeSuggestion', 'placeDetail',
        'directionDriving', 'directionTransit', 'directionWalking', 'directionBicycling',
        'matrix', 'weather'}),
    'didi': frozenset({'maps_textsearch', 'maps_regeocode', 'taxi_estimate', 'taxi_query_order'}),
}
PROVIDERS = ('tencentmap', 'didi', 'tongcheng', 'dingtalk')


def safe_result(value, secrets):
    """Redact credentials, including URL-encoded representations, before persistence/return."""
    if isinstance(value, str):
        for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
            for representation in {secret, quote(secret, safe=''), quote_plus(secret)}:
                value = value.replace(representation, '[REDACTED]')
        return value
    if isinstance(value, dict):
        return {safe_result(str(k), secrets): safe_result(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_result(v, secrets) for v in value]
    return value


def payload(result):
    structured = result.get('structured')
    if isinstance(structured, dict):
        return structured
    for content in result.get('content', []):
        if content.get('type') == 'text':
            try:
                parsed = json.loads(content.get('text', ''))
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                pass
    return {}


def has_point(value, latitude, longitude):
    try:
        lat, lng = float(latitude), float(longitude)
        if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
            return False
    except (ValueError, TypeError):
        return False

    def equal(a, b):
        try:
            return abs(float(a) - lat) < 0.000001 and abs(float(b) - lng) < 0.000001
        except (ValueError, TypeError):
            return False

    if isinstance(value, dict):
        if equal(value.get('lat', value.get('latitude')), value.get('lng', value.get('longitude'))):
            return True
        location = value.get('location')
        if isinstance(location, str) and ',' in location:
            pair = location.split(',')
            if len(pair) == 2 and equal(pair[1], pair[0]):
                return True
        return any(has_point(v, lat, lng) for v in value.values())
    if isinstance(value, list):
        return any(has_point(v, lat, lng) for v in value)
    return False


class MCPTransport:
    def __init__(self, timeout=30):
        self.timeout = timeout
        # Query-string keys must not appear in third-party INFO request logs.
        for name in ('httpx', 'httpx2', 'httpcore', 'mcp.client.streamable_http'):
            logging.getLogger(name).setLevel(logging.CRITICAL)

    async def request(self, url, tool=None, arguments=None):
        # Legacy initialize is explicit: no auto protocol fallback/retry around orders.
        async with httpx2.AsyncClient(follow_redirects=False, timeout=self.timeout) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1], read_timeout_seconds=self.timeout) as session:
                    await session.initialize()
                    if tool is None:
                        found, cursor = [], None
                        for _ in range(10):
                            from mcp.types import PaginatedRequestParams
                            result = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
                            found.extend(item.model_dump(by_alias=True, exclude_none=True) for item in result.tools)
                            cursor = result.next_cursor
                            if not cursor:
                                return found
                        raise ServiceError('upstream_unavailable', '工具列表过长，请联系服务管理员')
                    result = await session.call_tool(tool, arguments or {})
                    return {
                        'content': [item.model_dump(by_alias=True, exclude_none=True) for item in result.content],
                        'structured': result.structured_content,
                        'is_error': bool(result.is_error),
                    }


class Gateway:
    def __init__(self, store, settings, transport=None):
        self.store, self.settings = store, settings
        self.transport = transport or MCPTransport(settings.upstream_timeout_seconds)

    def status(self, uid):
        configured = self.store.credential_status(uid)
        return {
            'account_url': self.settings.public_base_url + '/account',
            'providers': {name: {
                'credential_saved': bool(configured.get(name, {}).get('configured')),
                'status': 'configured_not_tested' if name in READ_TOOLS and name in configured else 'pending_integration',
                'message': ('需要在个人配置页保存本人的Key，并通过实际工具调用验证连接。' if name in READ_TOOLS
                    else '尚无已核实的官方远程MCP端点，不能据此查询或发送。'),
            } for name in PROVIDERS},
            'system_calendar': 'waiting_calendar', 'scheduled_reminders': 'waiting_scheduler',
        }

    def _endpoint(self, uid, provider):
        if provider not in PROVIDERS:
            raise ServiceError('invalid_input', '服务名称无效')
        if provider not in READ_TOOLS:
            raise ServiceError('pending_integration', '该服务尚无已核实的远程MCP接入，操作尚未执行')
        credentials = self.store.get_credentials(uid, provider) or {}
        key = credentials.get('key')
        if not isinstance(key, str) or not key:
            raise ServiceError('pending_integration', '请在个人配置页连接本人的第三方服务，不要在聊天中发送Key')
        encoded = quote(key, safe='')
        url = ('https://mcp.map.qq.com/mcp?key=' + encoded + '&format=0' if provider == 'tencentmap'
            else 'https://mcp.didichuxing.com/mcp-servers?key=' + encoded)
        return url, list(credentials.values())

    async def _request(self, uid, provider, tool=None, arguments=None):
        url, secrets = self._endpoint(uid, provider)
        try:
            result = await self.transport.request(url, tool, arguments)
            if len(json.dumps(result, ensure_ascii=False)) > 500000:
                raise ServiceError('upstream_unavailable', '上游返回内容过大')
            return safe_result(result, secrets)
        except ServiceError:
            raise
        except Exception:
            # No exception repr/URL, which can contain the user's key.
            raise ServiceError('upstream_unavailable', '第三方服务未返回可确认结果，请检查个人连接状态') from None

    async def tools(self, uid, provider):
        try:
            actual = await self._request(uid, provider)
            return {'status': 'connected', 'provider': provider,
                    'tools': [tool for tool in actual if tool.get('name') in READ_TOOLS[provider]]}
        except ServiceError as error:
            return {'status': error.code, 'provider': provider, 'message': error.message}

    @staticmethod
    def require_trip(record):
        if record.get('status') != 'ready_for_route':
            raise ServiceError('needs_input', '请先完成当前补问或展会检索，确认目的地、出发地和时间后再规划')

    async def call(self, uid, provider, tool, arguments):
        if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 20000:
            raise ServiceError('invalid_input', '工具参数必须为合理大小的JSON对象')
        actual = await self._request(uid, provider)
        schema = next((entry.get('inputSchema', entry.get('input_schema')) for entry in actual if entry.get('name') == tool), None)
        if schema is None:
            raise ServiceError('pending_integration', '上游未提供所需工具，请以实际发现的工具列表为准')
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(arguments)
        except (ValidationError, SchemaError):
            raise ServiceError('invalid_input', '参数与上游工具实际Schema不符，请先查看工具列表') from None
        return await self._request(uid, provider, tool, arguments)

    async def query(self, uid, record_id, expected_revision, provider, tool, arguments):
        record = self.store.get_record(uid, record_id)
        if record['revision'] != expected_revision:
            raise ServiceError('conflict', '行程已变更，请读取当前版本')
        self.require_trip(record)
        if tool not in READ_TOOLS.get(provider, ()):
            raise ServiceError('invalid_input', '此工具不在只读工具清单中；真实叫车需先生成待确认方案')
        if tool == 'taxi_estimate':
            looked_up = [payload(x['result']) for x in record.get('queries', [])
                         if x['provider'] == 'didi' and x['tool'] == 'maps_textsearch' and not x['result'].get('is_error')
                         and x.get('trip_fields') == record['fields']]
            for prefix in ('from', 'to'):
                if not any(has_point(p, arguments.get(prefix + '_lat'), arguments.get(prefix + '_lng')) for p in looked_up):
                    raise ServiceError('invalid_input', '叫车坐标必须来自本次行程已返回的滴滴地点查询结果')
        result = await self.call(uid, provider, tool, arguments)
        receipt = {'id': str(uuid.uuid4()), 'provider': provider, 'tool': tool, 'arguments': arguments,
                   'result': result, 'received_at': time.time(), 'trip_fields': record['fields'].copy()}
        record['queries'] = (record.get('queries', []) + [receipt])[-20:]
        saved = self.store.save_record(uid, record, expected_revision)
        return {'record': saved, 'receipt': receipt, 'source': provider, 'untrusted_external_data': True}
