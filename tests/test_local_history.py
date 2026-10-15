import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import local_history
import chatgpt_tui


class LocalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"XDG_DATA_HOME": self.temp.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_local_images_survive_restart(self):
        sid = local_history.new_id()
        local_history.save(
            sid, server_id=None, title="New conversation",
            messages=[
                {"role": "user", "text": "/image beautiful city at night"},
                {"role": "image", "text": "/tmp/example.png"},
                {"role": "system", "text": "Saved."},
            ], last_image="/tmp/example.png"
        )
        snapshot = local_history.active()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["session_id"], sid)
        self.assertEqual(snapshot["messages"][0]["text"], "/image beautiful city at night")
        self.assertEqual(snapshot["last_image"], "/tmp/example.png")
        self.assertEqual(local_history.list_recent()[0]["title"], "/image beautiful city at night")

    def test_multiple_sessions_are_kept(self):
        old = local_history.new_id()
        new = local_history.new_id()
        local_history.save(old, server_id=None, title="Old",
                           messages=[{"role": "user", "text": "old"}])
        local_history.save(new, server_id=None, title="New",
                           messages=[{"role": "user", "text": "new"}])
        self.assertEqual(len(local_history.list_recent()), 2)
        self.assertEqual(local_history.active()["session_id"], new)

    def test_merge_caches_and_readonly_archives(self):
        remote = [{"id": "12345678", "title": "CLI server", "update_time": 100}]
        legacy = [{"id": "87654321", "title": "Older", "update_time": 20}]
        local_history.save(
            local_history.new_id(), server_id=None, title="Art",
            messages=[{"role": "user", "text": "/image tiger"}]
        )
        entries = local_history.merge_recent(remote, legacy=legacy)
        ids = [item["id"] for item in entries]
        self.assertIn("12345678", ids)
        self.assertIn("legacy:87654321", ids)
        self.assertTrue(any(item.startswith("local:") for item in ids))

    def test_merged_server_session_not_duplicated(self):
        local_history.save(local_history.new_id(), server_id="test-server-12345678",
                           title="Web", messages=[{"role": "user", "text": "hi"}])
        items = local_history.merge_recent(
            [{"id": "test-server-12345678", "title": "Web", "update_time": 42}]
        )
        self.assertEqual(len(items), 1)

    def test_private_file_permissions(self):
        sid = local_history.new_id()
        local_history.save(sid, server_id=None, title="Art",
                           messages=[{"role": "user", "text": "/image sun"}])
        self.assertEqual(local_history._path(sid).stat().st_mode & 0o777, 0o600)

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            local_history.load("../etc/passwd")

    def test_image_command_records_prompt_and_output(self):
        from image_backend import ImageResult
        tui = chatgpt_tui.ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        tui.native_image_preview = False
        result = ImageResult("/tmp/preview-test.png", "codex", "generate", 0.1)
        with patch.object(chatgpt_tui, "generate_image", return_value=result):
            tui.start_image_command("/image", "a flower garden")
            for _ in range(100):
                if not tui.image_busy:
                    break
                time.sleep(0.01)
        entry = local_history.active()
        self.assertIsNotNone(entry)
        self.assertTrue(any("a flower garden" in item["text"] for item in entry["messages"] if item["role"]=="user"))
        self.assertEqual(entry["last_image"], "/tmp/preview-test.png")
        self.assertIn("**Prompt:** a flower garden", "\n".join(m["text"] for m in entry["messages"]))

    def test_image_session_on_start_hydrates(self):
        sid = local_history.new_id()
        local_history.save(sid, server_id=None, title="Night city",
                           messages=[{"role": "user", "text": "/image night city"},
                                     {"role": "system", "text": "Done"}])
        snap = local_history.active()
        tui = chatgpt_tui.ChatTui(
            lambda: [], lambda _: {}, lambda *args: {},
            initial_messages=snap["messages"],
            local_session_id=snap["session_id"]
        )
        self.assertEqual(len(tui.messages), 2)
        self.assertEqual(tui.local_session_id, sid)


if __name__=="__main__":
    unittest.main()


class RecentSortTests(unittest.TestCase):
    def test_iso_numeric_and_float_times_sort_together(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            rows = local_history.merge_recent([
                {"id": "12345678", "title": "Old", "update_time": "2026-10-14T08:00:00Z"},
                {"id": "23456789", "title": "New", "update_time": 1792130000.0},
                {"id": "34567890", "title": "Middle", "update_time": "1792050000"},
            ])
            self.assertEqual(rows[0]["id"], "23456789")
            self.assertEqual(len(rows), 3)


class ImageRecoveryTests(unittest.TestCase):
    def test_old_image_files_are_discovered_without_invented_prompts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            folder = local_history.root().parent / "images"
            folder.mkdir(parents=True)
            file = folder / "20261015-233133-green-planet.png"
            file.write_bytes(b"test-file-metadata")
            self.assertEqual(local_history.recover_older_images(), 1)
            self.assertEqual(local_history.recover_older_images(), 0)
            entry = local_history.list_recent()[0]
            self.assertIn("Recovered image", entry["title"])
            self.assertIn("not preserved", entry["messages"][0]["text"])
            self.assertEqual(entry["messages"][1]["text"], str(file))
            self.assertIsNone(local_history.active())

    def test_no_false_recovery_for_manual_or_test_files(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
            folder = local_history.root().parent / "images"
            folder.mkdir(parents=True)
            (folder / "integration-smoke.png").write_bytes(b"test")
            self.assertEqual(local_history.recover_older_images(), 0)


class LegacyRoutingTests(unittest.TestCase):
    def test_replying_to_legacy_starts_new_project_chat_without_old_context(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"XDG_DATA_HOME": folder}
        ):
            tui = chatgpt_tui.ChatTui(
                lambda: [], lambda _: {}, lambda *args: {},
            )
            tui.conversation_id = None
            tui.legacy_read_only = True
            tui.messages = [
                {"role": "user", "text": "Old unrelated topic"},
                {"role": "assistant", "text": "Old cached response"},
            ]
            tui.input.text = "New request"
            with patch.object(chatgpt_tui.threading.Thread, "start"):
                tui.submit_prompt()
            self.assertFalse(tui.legacy_read_only)
            self.assertIsNone(tui.conversation_id)
            self.assertIn("not sent", tui.messages[0]["text"])
            self.assertFalse(any("Old unrelated" in m["text"] for m in tui.messages))
            self.assertEqual(tui.messages[-2]["text"], "New request")
