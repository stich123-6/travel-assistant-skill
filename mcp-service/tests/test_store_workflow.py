import json
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

try:
    from travel_mcp.store import ServiceError, Store
    from travel_mcp.workflow import TravelService
except ModuleNotFoundError:
    ServiceError = Store = TravelService = None


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Store, "persistence and workflow modules must exist")
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.key = Fernet.generate_key().decode()
        self.store = Store(self.directory, self.key)
        self.alice = self.store.create_user("alice", "alice-password")["id"]
        self.bob = self.store.create_user("bob", "bob-password")["id"]
        self.service = TravelService(self.store)

    def assert_error(self, code, operation):
        with self.assertRaises(ServiceError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)


class StoreTests(ServiceCase):
    def test_master_key_is_required(self):
        self.assert_error("configuration_error", lambda: Store(self.directory, ""))

    def test_credentials_are_encrypted_persistent_and_never_echoed(self):
        secret = "private-provider-token-99881"
        self.store.set_credentials(self.alice, "tencent", {"api_key": secret})
        status = self.store.credential_status(self.alice)
        self.assertTrue(status["tencent"]["configured"])
        self.assertNotIn(secret, json.dumps(status))
        self.assertIsNone(self.store.get_credentials(self.bob, "tencent"))
        restarted = Store(self.directory, self.key)
        self.assertEqual({"api_key": secret}, restarted.get_credentials(self.alice, "tencent"))
        self.assertNotIn(secret.encode(), (self.directory / "travel.sqlite3").read_bytes())
        self.store.delete_credentials(self.alice, "tencent")
        self.assertIsNone(self.store.get_credentials(self.alice, "tencent"))

    def test_passwords_are_hashed_and_authentication_is_user_scoped(self):
        self.assertEqual(self.alice, self.store.authenticate("alice", "alice-password"))
        self.assertIsNone(self.store.authenticate("alice", "incorrect"))
        self.assertIsNone(self.store.authenticate("missing", "alice-password"))
        self.assertEqual({"id": self.alice, "username": "alice"}, self.store.get_user(self.alice))
        self.assertNotIn(b"alice-password", (self.directory / "travel.sqlite3").read_bytes())

    def test_passwords_preserve_significant_whitespace(self):
        user = self.store.create_user("spaced", " password with spaces ")
        self.assertEqual(user["id"], self.store.authenticate("spaced", " password with spaces "))
        self.assertIsNone(self.store.authenticate("spaced", "password with spaces"))

    def test_records_persist_and_use_optimistic_concurrency(self):
        original = self.store.save_record(self.alice, {"intent": "memo", "fields": {"memo_content": "one"}}, None)
        self.assertEqual(1, original["revision"])
        updated = dict(original, fields={"memo_content": "two"})
        saved = self.store.save_record(self.alice, updated, 1)
        self.assertEqual(2, saved["revision"])
        self.assert_error("conflict", lambda: self.store.save_record(self.alice, updated, 1))
        self.assert_error("conflict", lambda: self.store.save_record(self.alice, updated, None))
        self.assertEqual("two", Store(self.directory, self.key).get_record(self.alice, saved["id"])["fields"]["memo_content"])

    def test_records_cannot_be_read_or_overwritten_by_other_users(self):
        record = self.store.save_record(self.alice, {"intent": "memo"}, None)
        self.assert_error("not_found", lambda: self.store.get_record(self.bob, record["id"]))
        self.assert_error("not_found", lambda: self.store.save_record(self.bob, record, 1))
        self.assertEqual([], self.store.list_records(self.bob))

    def test_origins_have_stable_ids_revisions_and_refreshed_mobile_cards(self):
        missing = self.store.origin(self.alice, "add", label="家")
        self.assertEqual("needs_input", missing["status"])
        self.assertEqual([], missing["origins"])
        added = self.store.origin(self.alice, "add", label="家", address="杭州市测试路1号")
        origin = added["origins"][0]
        self.assertIn("mobile_form", added)
        self.assertNotIn("selected_origin_id", added)
        changed = self.store.origin(self.alice, "update", origin_id=origin["id"], address="杭州市测试路2号", expected_revision=1)
        self.assertEqual(origin["id"], changed["origins"][0]["id"])
        self.assertEqual(2, changed["origins"][0]["revision"])
        self.assert_error("conflict", lambda: self.store.origin(self.alice, "update", origin_id=origin["id"], label="家庭", expected_revision=1))
        self.assert_error("not_found", lambda: self.store.origin(self.bob, "delete", origin_id=origin["id"], expected_revision=2))
        self.assertEqual(1, len(Store(self.directory, self.key).origin(self.alice, "list")["origins"]))
        deleted = self.store.origin(self.alice, "delete", origin_id=origin["id"], expected_revision=2)
        self.assertEqual([], deleted["origins"])


class WorkflowTests(ServiceCase):
    def test_ready_trips_include_pending_preparation_checklists(self):
        base = {"origin_address": "杭州市出发路1号", "departure_at": "2026-11-03T08:30:00+08:00"}
        cases = (
            ("local", {"destination_address": "杭州市拜访路1号"}, {"准备拜访材料"}),
            ("travel", {"destination_address": "上海市目的地路1号"}, {"携带身份证件", "核对交通和住宿安排"}),
            ("exhibition", {"event_address": "上海市场馆路1号", "address_only_authorized": True}, {"携带身份证件", "准备门票或参会凭证", "准备参会材料"}),
        )
        for intent, extra, required in cases:
            with self.subTest(intent=intent):
                record = self.service.intake(self.alice, "安排这次出行", dict(base, intent=intent, **extra))
                self.assertEqual("plan_route", record["next_action"]["type"])
                checklist = [item for item in record["preparation"] if item["kind"] == "checklist"]
                labels = {item["label"] for item in checklist}
                self.assertTrue((required | {"给手机和充电宝充电", "预留提前到场时间"}) <= labels)
                self.assertTrue(all(item["status"] == "pending" for item in checklist))

    def test_missing_memo_and_message_fields_keep_needs_input_status(self):
        for message, field in (("提醒我带名片", "reminder_at"), ("钉钉通知同事", "recipient")):
            with self.subTest(message=message):
                record = self.service.intake(self.alice, message)
                self.assertEqual("needs_input", record["status"])
                self.assertEqual("ask_fields", record["next_action"]["type"])
                self.assertEqual(field, record["mobile_form"]["questions"][0]["field"])

    def test_dingtalk_message_request_wins_over_exhibition_message_content(self):
        record = self.service.intake(self.alice, "钉钉告诉张三，展会改期了", {"recipient": "张三"})
        self.assertEqual("dingtalk_message", record["intent"])
        self.assertEqual("pending_integration", record["next_action"]["type"])
        explicit = self.service.intake(self.alice, "钉钉告诉张三，展会改期了", {"intent": "exhibition"})
        self.assertEqual("exhibition", explicit["intent"])
        self.assertEqual("event_name", explicit["mobile_form"]["questions"][0]["field"])

    def test_an_ordinary_exhibition_category_is_not_mistaken_for_a_local_visit(self):
        record = self.service.intake(self.alice, "想去广州看机器人展")
        self.assertEqual("exhibition", record["intent"])
        self.assertEqual("event_name", record["mobile_form"]["questions"][0]["field"])

    def test_exhibition_without_specific_name_asks_only_its_name(self):
        record = self.service.intake(self.alice, "我想参加上海的科技展", {"city": "上海", "category": "科技展"})
        self.assertEqual("exhibition", record["intent"])
        self.assertEqual(["event_name"], [question["field"] for question in record["mobile_form"]["questions"]])
        self.assertEqual("你要参加的展会叫什么名字？", record["mobile_form"]["questions"][0]["prompt"])
        self.assertEqual("上海", record["fields"]["city"])

    def test_named_exhibition_requests_host_search_before_address(self):
        record = self.service.intake(self.alice, "参加展会", {"event_name": "第二十届测试工业博览会", "city": "上海", "event_date": "2026-11-03"})
        self.assertEqual("search_exhibition", record["next_action"]["type"])
        for value in ("第二十届测试工业博览会", "上海", "2026-11-03"):
            self.assertIn(value, record["next_action"]["query"])
        self.assertNotEqual("found", record.get("search", {}).get("status"))
        failed = self.service.record_search(self.alice, record["id"], "no_result", expected_revision=record["revision"])
        self.assertEqual("event_address", failed["mobile_form"]["questions"][0]["field"])

    def test_failed_search_accepts_user_address_and_requires_explicit_origin(self):
        self.store.origin(self.alice, "add", label="公司", address="杭州市公司路1号")
        record = self.service.intake(self.alice, "参展", {"event_name": "测试大会"})
        record = self.service.record_search(self.alice, record["id"], "unavailable", expected_revision=record["revision"])
        record = self.service.update(self.alice, record["id"], {"event_address": "上海市场馆路1号"}, record["revision"])
        self.assertEqual("user", record["fields"]["event_address_source"])
        question = record["mobile_form"]["questions"][0]
        self.assertEqual("origin_id", question["field"])
        self.assertIn("公司", json.dumps(question, ensure_ascii=False))
        self.assertNotIn("origin_id", record["fields"])

    def test_found_result_supplies_venue_without_repeating_name(self):
        record = self.service.intake(self.alice, "参展", {"event_name": "测试大会"})
        result = self.service.record_search(self.alice, record["id"], "found", {"event_name": "测试大会", "name": "测试馆", "address": "上海市场馆路1号"}, "https://example.test/event", record["revision"])
        self.assertEqual("search_result", result["fields"]["event_address_source"])
        self.assertEqual("origin_id", result["mobile_form"]["questions"][0]["field"])
        self.assertEqual("https://example.test/event", result["search"]["source_url"])

    def test_address_only_requires_explicit_authorization(self):
        record = self.service.intake(self.alice, "参展", {"event_address": "上海市测试路1号"})
        self.assertEqual("event_name", record["mobile_form"]["questions"][0]["field"])
        allowed = self.service.update(self.alice, record["id"], {"address_only_authorized": True}, record["revision"])
        self.assertEqual("origin_id", allowed["mobile_form"]["questions"][0]["field"])
        self.assertFalse(allowed["event_verified"])

    def test_workflow_rejects_unknown_fields_credentials_and_invalid_datetimes(self):
        for fields in ({"api_key": "must-not-persist"}, {"user_id": self.bob}, {"unexpected": "value"}, {"departure_at": "tomorrow"}, {"event_date": "2026-02-31"}):
            with self.subTest(fields=fields):
                self.assert_error("invalid_input", lambda: self.service.intake(self.alice, "参展", fields))
        self.assertEqual([], self.store.list_records(self.alice))

    def test_workflow_updates_require_matching_revision_and_ownership(self):
        record = self.service.intake(self.alice, "记一下要带名片")
        self.assert_error("not_found", lambda: self.service.update(self.bob, record["id"], {"memo_content": "hijack"}, record["revision"]))
        self.service.update(self.alice, record["id"], {"memo_content": "带名片和水"}, record["revision"])
        self.assert_error("conflict", lambda: self.service.update(self.alice, record["id"], {"memo_content": "old"}, record["revision"]))

    def test_memos_calendar_and_messages_do_not_claim_delivery(self):
        memo = self.service.intake(self.alice, "提醒我带名片", {"reminder_at": "2026-11-03T08:30:00+08:00"})
        self.assertEqual("waiting_scheduler", memo["status"])
        self.assertEqual("waiting_scheduler", memo["next_action"]["type"])
        message = self.service.intake(self.alice, "钉钉通知同事", {"recipient": "张三", "message_content": "明天开会"})
        self.assertEqual("pending_integration", message["status"])
        local = self.service.intake(self.alice, "拜访客户", {"destination_address": "杭州市测试路1号", "origin_address": "杭州市出发路1号", "departure_at": "2026-11-03T08:30:00+08:00", "add_calendar": True})
        self.assertEqual("plan_route", local["next_action"]["type"])
        self.assertIn({"kind": "calendar", "status": "waiting_calendar"}, local["preparation"])


if __name__ == "__main__":
    unittest.main()
