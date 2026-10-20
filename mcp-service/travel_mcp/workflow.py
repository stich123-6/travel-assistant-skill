"""Deterministic intake state, with host search and integrations stated explicitly."""

import copy
import re
from datetime import date, datetime
from urllib.parse import urlparse

from .store import ServiceError


INTENTS = {"exhibition", "local", "travel", "memo", "dingtalk_message"}
DATE_FIELDS = {"event_date", "travel_date", "return_date"}
DATETIME_FIELDS = {"start_at", "end_at", "departure_at", "arrival_at", "reminder_at", "start_time", "end_time"}
BOOLEAN_FIELDS = {"address_only_authorized", "add_calendar", "notify_dingtalk"}
TEXT_FIELDS = {
    "intent", "event_name", "city", "category", "event_address", "origin_id",
    "origin_label", "origin_address", "destination_name", "destination_address",
    "destination_city", "travel_mode", "memo_content", "recipient", "message_content",
    "timezone", "notes",
}
ALLOWED_FIELDS = DATE_FIELDS | DATETIME_FIELDS | BOOLEAN_FIELDS | TEXT_FIELDS | {"budget", "people_count"}


class TravelService:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _fields(fields):
        if fields is None:
            return {}
        if not isinstance(fields, dict) or not set(fields) <= ALLOWED_FIELDS:
            raise ServiceError("invalid_input", "字段无效；第三方凭证只能在个人配置页填写")
        clean = {}
        for key, value in fields.items():
            if value is None:
                clean[key] = None
                continue
            if key in BOOLEAN_FIELDS:
                if type(value) is not bool:
                    raise ServiceError("invalid_input", f"{key}必须为布尔值")
            elif key in {"budget", "people_count"}:
                if type(value) not in {int, float} or value <= 0 or value != value or value == float("inf"):
                    raise ServiceError("invalid_input", f"{key}必须为正数")
                if key == "people_count" and type(value) is not int:
                    raise ServiceError("invalid_input", "人数必须为整数")
            elif not isinstance(value, str) or len(value) > 10000:
                raise ServiceError("invalid_input", f"{key}必须为文本")
            else:
                value = value.strip()
                if key in DATE_FIELDS | DATETIME_FIELDS and value:
                    try:
                        if key in DATE_FIELDS:
                            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                                raise ValueError()
                            date.fromisoformat(value)
                        else:
                            if not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
                                raise ValueError()
                            datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError as exc:
                        raise ServiceError("invalid_input", f"{key}需要有效的ISO日期或日期时间") from exc
                if key == "intent" and value not in INTENTS:
                    raise ServiceError("invalid_input", "意图无效")
            clean[key] = value
        return clean

    @staticmethod
    def _intent(message, fields):
        if fields.get("intent"):
            return fields["intent"]
        if any(word in message for word in ("钉钉", "发消息", "通知同事")):
            return "dingtalk_message"
        if fields.get("event_name") or fields.get("category") or any(word in message for word in ("展会", "展览", "博览", "参展", "科技展", "exhibition", "广交会", "进博会")) or re.search(r"(?:参加|参观|去看|看|逛)[^。！？\n]{0,50}展", message):
            return "exhibition"
        if fields.get("memo_content") or fields.get("reminder_at") or any(word in message for word in ("备忘", "记一下", "提醒我")):
            return "memo"
        if any(word in message for word in ("出差", "外地", "高铁", "飞机", "机票", "酒店", "旅行", "旅游")):
            return "travel"
        return "local"

    def intake(self, user_id, message, fields=None):
        if not isinstance(message, str) or not message.strip() or len(message) > 10000:
            raise ServiceError("invalid_input", "请提供有效的需求描述")
        fields = self._fields(fields)
        intent = self._intent(message, fields)
        if intent == "memo" and not fields.get("memo_content"):
            fields["memo_content"] = message.strip()
        if intent == "dingtalk_message" and not fields.get("message_content"):
            fields["message_content"] = message.strip()
        record = {"intent": intent, "message": message.strip(), "fields": fields}
        if fields.get("event_address"):
            fields["event_address_source"] = "user"
        self._advance(user_id, record)
        return self.store.save_record(user_id, record, None)

    def update(self, user_id, record_id, patch, expected_revision):
        record = self.store.get_record(user_id, record_id)
        if record.get("intent") not in INTENTS:
            raise ServiceError("invalid_input", "此记录不能通过行程补充工具修改")
        patch = self._fields(patch)
        fields = record["fields"]
        if any(key in patch and patch[key] != fields.get(key) for key in ("event_name", "city", "event_date")):
            record.pop("search", None)
            if fields.get("event_address_source") == "search_result":
                fields.pop("event_address", None)
                fields.pop("event_address_source", None)
        if "origin_id" in patch and patch["origin_id"] != fields.get("origin_id"):
            fields.pop("origin_address", None)
            fields.pop("origin_label", None)
        for key, value in patch.items():
            if value is None or value == "":
                fields.pop(key, None)
            else:
                fields[key] = value
        if "event_address" in patch:
            if fields.get("event_address"):
                fields["event_address_source"] = "user"
            else:
                fields.pop("event_address_source", None)
        if patch.get("intent"):
            record["intent"] = patch["intent"]
        self._advance(user_id, record)
        return self.store.save_record(user_id, record, expected_revision)

    def record_search(self, user_id, record_id, status, venue=None, source_url=None, expected_revision=None):
        record = self.store.get_record(user_id, record_id)
        if record.get("intent") != "exhibition" or not self._has_event_name(record["fields"]):
            raise ServiceError("invalid_input", "先提供具体展会名称才能记录展会检索")
        if status not in {"found", "no_result", "unavailable", "mismatch"}:
            raise ServiceError("invalid_input", "检索状态无效")
        if source_url is not None:
            if not isinstance(source_url, str) or len(source_url) > 4000:
                raise ServiceError("invalid_input", "来源链接无效")
            parsed = urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ServiceError("invalid_input", "来源链接必须为HTTP或HTTPS")
        if venue is not None:
            if not isinstance(venue, dict) or not set(venue) <= {"name", "address", "city", "event_name"} or any(not isinstance(value, str) or len(value) > 10000 for value in venue.values()):
                raise ServiceError("invalid_input", "场馆结果无效")
            venue = {key: value.strip() for key, value in venue.items()}
        if status == "found":
            if not venue or not venue.get("address"):
                raise ServiceError("invalid_input", "已找到的展会结果必须包含实际场馆地址")
            if venue.get("event_name") and venue["event_name"] != record["fields"]["event_name"]:
                status = "mismatch"
        record["search"] = {"status": status, "venue": venue, "source_url": source_url, "reported_by": "host"}
        if status == "found" and record["fields"].get("event_address_source") != "user":
            record["fields"]["event_address"] = venue["address"]
            record["fields"]["event_address_source"] = "search_result"
        elif status != "found" and record["fields"].get("event_address_source") == "search_result":
            record["fields"].pop("event_address", None)
            record["fields"].pop("event_address_source", None)
        self._advance(user_id, record)
        return self.store.save_record(user_id, record, expected_revision)

    @staticmethod
    def _has_event_name(fields):
        name = fields.get("event_name")
        return bool(name and name not in {fields.get("city"), fields.get("category"), "展会", "展览", "博览会", "展览会", "科技展", "行业展会", "不知道", "未知"})

    @staticmethod
    def _ask(record, field, prompt, choices=None):
        question = {"field": field, "prompt": prompt}
        if choices is not None:
            question["choices"] = choices
        record["status"] = "needs_input"
        record["next_action"] = {"type": "ask_fields", "fields": [field], "prompt": prompt}
        record["mobile_form"]["questions"] = [question]

    def _advance(self, user_id, record):
        fields, intent = record["fields"], record["intent"]
        record["preparation"] = []
        record["mobile_form"] = {"title": "行程信息", "questions": [], "known": copy.deepcopy(fields)}
        if fields.get("add_calendar"):
            record["preparation"].append({"kind": "calendar", "status": "waiting_calendar"})
        if fields.get("notify_dingtalk"):
            record["preparation"].append({"kind": "dingtalk_message", "status": "pending_integration"})
        if intent == "memo":
            record["preparation"].append({"kind": "reminder", "status": "waiting_scheduler"})
            if not fields.get("reminder_at"):
                self._ask(record, "reminder_at", "需要在什么日期和时间提醒你？")
            else:
                record["next_action"] = {"type": "waiting_scheduler", "message": "备忘已保存，真正的定时提醒仍待接入。"}
                record["status"] = "waiting_scheduler"
            return
        if intent == "dingtalk_message":
            record["preparation"].append({"kind": "dingtalk_message", "status": "pending_integration"})
            if not fields.get("recipient"):
                self._ask(record, "recipient", "要通过钉钉通知谁？")
            else:
                record["next_action"] = {"type": "pending_integration", "message": "通知内容已保存，钉钉消息接口仍待接入，消息尚未发送。"}
                record["status"] = "pending_integration"
            return
        if intent == "exhibition":
            has_name = self._has_event_name(fields)
            bypass = fields.get("address_only_authorized") is True and bool(fields.get("event_address"))
            record["event_verified"] = bool(has_name and record.get("search", {}).get("status") == "found")
            if not has_name and not bypass:
                self._ask(record, "event_name", "你要参加的展会叫什么名字？")
                return
            if has_name and record.get("search", {}).get("status") not in {"found", "no_result", "unavailable", "mismatch"}:
                query = " ".join(str(fields[key]) for key in ("event_name", "city", "event_date") if fields.get(key))
                record["status"] = "waiting_search"
                record["next_action"] = {"type": "search_exhibition", "query": query, "executor": "host", "message": "请由宿主按具体展会名称检索并回传实际结果。"}
                return
            if not fields.get("event_address"):
                self._ask(record, "event_address", "未获得可用的展会场馆地址，请提供具体地址。")
                return
        elif not fields.get("destination_address"):
            self._ask(record, "destination_address", "目的地的具体地址是什么？")
            return
        if not fields.get("origin_address"):
            origins = self.store.origin(user_id, "list")["origins"]
            origin_id = fields.get("origin_id")
            selected = next((origin for origin in origins if origin["id"] == origin_id), None)
            if selected:
                fields["origin_address"] = selected["address"]
                fields["origin_label"] = selected["label"]
                record["mobile_form"]["known"] = copy.deepcopy(fields)
            elif origin_id in {"home", "company", "other"}:
                self._ask(record, "origin_address", "这次选定出发地点的具体地址是什么？")
                return
            elif origin_id:
                raise ServiceError("not_found", "选定的出发地点不存在，请重新选择")
            else:
                choices = [{"value": origin["id"], "label": origin["label"], "address": origin["address"]} for origin in origins]
                choices.extend([{"value": "home", "label": "家"}, {"value": "company", "label": "公司"}, {"value": "other", "label": "其他地点"}])
                self._ask(record, "origin_id", "这次从哪里出发？请选择已保存地点、家、公司或其他地点。", choices)
                return
        if not fields.get("departure_at"):
            self._ask(record, "departure_at", "计划在哪一天、什么时间出发？")
            return
        record["status"] = "ready_for_route"
        record["preparation"].append({"kind": "route", "status": "waiting_route"})
        checklist = {
            "local": ["按需携带身份证件", "准备拜访材料"],
            "travel": ["携带身份证件", "核对交通和住宿安排", "准备随身材料"],
            "exhibition": ["携带身份证件", "准备门票或参会凭证", "准备参会材料"],
        }[intent] + ["给手机和充电宝充电", "预留提前到场时间"]
        record["preparation"].extend({"kind": "checklist", "label": label, "status": "pending"} for label in checklist)
        record["next_action"] = {"type": "plan_route", "origin": fields["origin_address"], "destination": fields.get("event_address") if intent == "exhibition" else fields["destination_address"], "departure_at": fields["departure_at"]}
