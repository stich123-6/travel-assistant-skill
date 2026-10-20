"""A concrete, expiring taxi quote must precede one authorized order attempt."""

import time
import json
import sqlite3
from .store import ServiceError
from .upstream import payload


class Actions:
    def __init__(self, store, gateway):
        self.store, self.gateway = store, gateway
        with store.connection() as connection:
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS taxi_one_proposal_per_quote
                ON records(user_id, json_extract(payload, '$.arguments.estimate_trace_id'))
                WHERE json_extract(payload, '$.kind') = 'taxi_create_order'""")

    def _existing(self, uid, trace_id):
        with self.store.connection() as connection:
            row = connection.execute("""SELECT payload FROM records WHERE user_id=?
                AND json_extract(payload, '$.kind')='taxi_create_order'
                AND json_extract(payload, '$.arguments.estimate_trace_id')=?""", (uid, trace_id)).fetchone()
        return json.loads(row['payload']) if row else None

    def prepare_taxi(self, uid, record_id, expected_revision, estimate_receipt_id, product_category):
        record = self.store.get_record(uid, record_id)
        self.gateway.require_trip(record)
        if record['revision'] != expected_revision:
            raise ServiceError('conflict', '行程已变更，请读取当前版本')
        receipt = next((x for x in record.get('queries', []) if x['id'] == estimate_receipt_id
                        and x['provider'] == 'didi' and x['tool'] == 'taxi_estimate'), None)
        if not receipt or receipt['result'].get('is_error') or receipt.get('trip_fields') != record['fields']:
            raise ServiceError('invalid_input', '需要与当前行程一致的真实估价结果')
        quote = payload(receipt['result'])
        chosen = next((x for x in quote.get('items', []) if str(x.get('productCategory')) == str(product_category)), None)
        if not chosen or not chosen.get('priceText') or not quote.get('traceId'):
            raise ServiceError('invalid_input', '请从实际返回的车型和价格中选择，不得编造报价')
        existing = self._existing(uid, quote['traceId'])
        if existing:
            return existing
        if time.time() - receipt['received_at'] > 600:
            raise ServiceError('expired', '估价已过期，请重新查询')
        proposal = {
            'intent': 'external_action', 'kind': 'taxi_create_order', 'trip_id': record_id,
            'trip_revision': record['revision'], 'status': 'needs_confirmation', 'expires_at': receipt['received_at'] + 600,
            'arguments': {'estimate_trace_id': quote['traceId'], 'product_category': chosen['productCategory']},
            'confirmation': {'route': receipt['arguments'], 'product': chosen, 'required_text': '确认按此方案叫车'},
            'message': '请展示起终点、车型和价格，获得用户对本方案的明确同意后才可执行。',
        }
        try:
            return self.store.save_record(uid, proposal, None)
        except sqlite3.IntegrityError:
            # Unique index is the final arbiter for simultaneous prepare requests.
            existing = self._existing(uid, quote['traceId'])
            if existing:
                return existing
            raise ServiceError('conflict', '方案保存冲突，请读取已有方案') from None

    async def execute_taxi(self, uid, proposal_id, expected_revision, approval_text):
        proposal = self.store.get_record(uid, proposal_id)
        if proposal.get('kind') != 'taxi_create_order' or proposal.get('intent') != 'external_action':
            raise ServiceError('invalid_input', '待确认操作无效')
        if proposal['status'] != 'needs_confirmation':
            return proposal  # Includes executing/unknown; never submit it again.
        if approval_text != '确认按此方案叫车':
            raise ServiceError('needs_confirmation', '必须先展示本方案并取得用户明确确认')
        if time.time() > proposal['expires_at']:
            raise ServiceError('expired', '本方案已过期，请重新估价')
        trip = self.store.get_record(uid, proposal['trip_id'])
        if trip['revision'] != proposal['trip_revision']:
            raise ServiceError('conflict', '行程已变化，请重新估价和确认')
        proposal['status'] = 'executing'
        proposal = self.store.save_record(uid, proposal, expected_revision)  # Atomic claim before external call.
        try:
            result = await self.gateway.call(uid, 'didi', 'taxi_create_order', proposal['arguments'])
            proposal['result'] = result
            proposal['status'] = 'failed' if result.get('is_error') else 'submitted'
            proposal['message'] = '请以返回的订单信息为准；后续接单状态需查询。'
        except Exception:
            proposal['status'] = 'unknown'
            proposal['message'] = '提交结果未确认，禁止自动重试；请先在滴滴或订单查询中核实。'
        return self.store.save_record(uid, proposal, proposal['revision'])
