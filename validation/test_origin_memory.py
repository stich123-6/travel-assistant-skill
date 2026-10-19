"""Local persistence and card-data checks; no external services are called."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "travel-assistant" / "scripts" / "origin_memory.py"
spec = importlib.util.spec_from_file_location("origin_memory", SCRIPT)
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


class OriginMemoryTests(unittest.TestCase):
    def setUp(self):
        validation_dir = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix="origin-memory-test-", dir=validation_dir)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(validation_dir))
        self.addCleanup(self.temp.cleanup)

    def run_action(self, action="list", user="user-a", **kwargs):
        return memory.execute(self.root, user, action, **kwargs)

    def test_missing_address_requests_input_without_saving(self):
        result = self.run_action("add", label="公司")
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("address", result["form"]["required"])
        self.assertEqual(result["form"]["values"]["label"], "公司")
        self.assertFalse(memory.profile_path(self.root, "user-a").exists())

    def test_add_update_delete_persist_and_refresh_card(self):
        added = self.run_action("add", label="公司", address="杭州市滨江区示例路1号")
        origin_id = added["origin"]["id"]
        self.assertEqual(added["revision"], 1)
        self.assertEqual(added["card"]["options"][0]["label"], "公司")
        self.assertIsNone(added["card"]["selected_id"])
        self.assertEqual(self.run_action()["locations"][0]["id"], origin_id)

        updated = self.run_action("update", origin_id=origin_id, label="杭州公司", address="杭州市西湖区示例路2号", expected_revision=1)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["origin"]["id"], origin_id)
        self.assertEqual(updated["card"]["options"][0]["label"], "杭州公司")
        self.assertEqual(updated["card"]["options"][0]["address"], "杭州市西湖区示例路2号")

        deleted = self.run_action("delete", origin_id=origin_id, expected_revision=2)
        self.assertEqual(deleted["revision"], 3)
        self.assertFalse(deleted["locations"])
        self.assertFalse(any(option.get("origin_id") == origin_id for option in deleted["card"]["options"]))
        self.assertEqual(self.run_action()["locations"], [])

    def test_users_are_isolated_even_for_path_like_ids(self):
        self.run_action("add", label="家", address="用户A地址")
        self.assertEqual(self.run_action(user="../user-b")["locations"], [])
        profile = memory.profile_path(self.root, "../user-b")
        self.assertTrue(profile.resolve().is_relative_to(self.root.resolve()))

    def test_duplicate_is_idempotent_and_conflict_cannot_overwrite(self):
        first = self.run_action("add", label="家", address="杭州地址一")
        second = self.run_action("add", label="家", address="杭州地址一")
        self.assertEqual(second["origin"]["id"], first["origin"]["id"])
        self.assertEqual(second["revision"], 1)
        with self.assertRaisesRegex(memory.MemoryError, "duplicate_label"):
            self.run_action("add", label="家", address="杭州地址二")
        self.assertEqual(self.run_action()["locations"][0]["address"], "杭州地址一")

    def test_stale_card_cannot_modify_current_data(self):
        first = self.run_action("add", label="家", address="杭州地址一")
        self.run_action("add", label="公司", address="杭州地址二")
        with self.assertRaisesRegex(memory.MemoryError, "stale_revision"):
            self.run_action("delete", origin_id=first["origin"]["id"], expected_revision=1)
        self.assertEqual(len(self.run_action()["locations"]), 2)

    def test_failed_replace_keeps_existing_profile(self):
        first = self.run_action("add", label="家", address="杭州地址一")
        profile = memory.profile_path(self.root, "user-a")
        before = profile.read_bytes()
        with patch.object(memory.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(memory.MemoryError, "save_failed"):
                self.run_action("update", origin_id=first["origin"]["id"], address="杭州地址二")
        self.assertEqual(profile.read_bytes(), before)
        self.assertFalse(profile.with_suffix(".lock").exists())

    def test_empty_address_is_rejected_and_other_settings_survive(self):
        first = self.run_action("add", label="公司", address="杭州地址一")
        profile = memory.profile_path(self.root, "user-a")
        data = json.loads(profile.read_text(encoding="utf-8"))
        data["system_calendar"]["calendar_id"] = "existing-calendar"
        profile.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(memory.MemoryError, "invalid_address"):
            self.run_action("update", origin_id=first["origin"]["id"], address="  ")
        self.run_action("update", origin_id=first["origin"]["id"], label="杭州公司")
        saved = json.loads(profile.read_text(encoding="utf-8"))
        self.assertEqual(saved["system_calendar"]["calendar_id"], "existing-calendar")

    def test_corrupted_profile_and_existing_lock_are_not_overwritten(self):
        profile = memory.profile_path(self.root, "user-a")
        profile.parent.mkdir(parents=True)
        profile.write_text("broken json", encoding="utf-8")
        with self.assertRaisesRegex(memory.MemoryError, "invalid_profile"):
            self.run_action("add", label="家", address="杭州地址")
        self.assertEqual(profile.read_text(encoding="utf-8"), "broken json")
        profile.unlink()
        lock = profile.with_suffix(".lock")
        lock.write_text("other writer", encoding="utf-8")
        with self.assertRaisesRegex(memory.MemoryError, "memory_busy"):
            self.run_action("add", label="家", address="杭州地址")
        self.assertEqual(lock.read_text(encoding="utf-8"), "other writer")

    def test_cli_can_derive_label_from_supplied_location(self):
        command = [sys.executable, "-X", "utf8", str(SCRIPT), "--data-dir", str(self.root), "--user-id", "user-a", "add", "--address", "杭州东站"]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
        result = json.loads(completed.stdout)
        self.assertEqual(result["origin"]["label"], "杭州东站")
        self.assertEqual(result["status"], "saved")


if __name__ == "__main__":
    unittest.main()
