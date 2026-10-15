import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from chatgpt_tui import ChatTui
from cooldown import retry_after_seconds, remaining_seconds, format_remaining, display_rate_limit
import local_history


class CooldownTests(unittest.TestCase):
    def test_parsing_and_seconds(self):
        for value in (11, 14, 44):
            self.assertEqual(retry_after_seconds(
                f"ChatGPT web rate limit reached; retry after {value} seconds"), value)
        self.assertIsNone(retry_after_seconds("Temporary networking error"))
        self.assertEqual(remaining_seconds(1800, 1790), 10)
        self.assertEqual(remaining_seconds(1800, 1800), 0)
        self.assertEqual(format_remaining(65), "1m 05s")
        self.assertEqual(format_remaining(3661), "1h 01m 01s")
        original = "*Request stopped: ChatGPT web rate limit reached; retry after 44 seconds*"
        self.assertIn("retry in 10s", display_rate_limit(original, 1800, now=1790))
        self.assertIn("ready to retry", display_rate_limit(original, 1800, now=1800))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_arrow_recall_and_restore_draft(self):
        tui = ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        tui.history_entries = ["third", "second", "first"]
        tui.input.text = "draft"
        for expected in ("third", "second", "first", "first"):
            tui.move_prompt_history(-1)
            self.assertEqual(tui.input.text, expected)
        for expected in ("second", "third", "draft"):
            tui.move_prompt_history(1)
            self.assertEqual(tui.input.text, expected)

    def test_saved_history_from_last_session(self):
        local_history.save(local_history.new_id(), server_id=None, title="Test",
                           messages=[{"role": "user", "text": "from yesterday"}])
        tui = ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        self.assertIn("from yesterday", tui.history_entries)
        tui.record_prompt_history("fresh")
        self.assertEqual(tui.history_entries[0], "fresh")

    def test_edit_resets_navigation(self):
        tui = ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        tui.history_entries = ["last"]
        tui.input.text = "draft"
        tui.move_prompt_history(-1)
        self.assertEqual(tui.history_position, 0)
        tui.on_input_changed(tui.input.buffer)
        self.assertEqual(tui.history_position, -1)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    @staticmethod
    def wait(tui):
        for _ in range(100):
            if not tui.busy:
                return
            time.sleep(0.01)
        raise AssertionError("worker still running")

    def test_failed_request_keeps_prompt_and_dynamic_inline_message(self):
        send = Mock(side_effect=RuntimeError(
            "ChatGPT web rate limit reached; retry after 14 seconds"))
        tui = ChatTui(lambda: [], lambda _: {}, send)
        tui.input.text = "can you use my mcps?"
        tui.submit_prompt()
        self.wait(tui)
        self.assertEqual(send.call_count, 1)
        self.assertGreater(tui.cooldown_remaining(), 0)
        self.assertEqual(tui.input.text, "can you use my mcps?")
        self.assertIn("retry in", tui.render_transcript())
        self.assertIn("RATE LIMIT", "".join(x for _, x in tui.render_metadata()))
        self.assertEqual(tui.committed_message_count, 0)
        tui.submit_prompt()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(tui.input.text, "can you use my mcps?")
        self.assertEqual(len(tui.messages), 2)

    def test_restart_and_expiry(self):
        send = Mock(side_effect=RuntimeError(
            "ChatGPT web rate limit reached; retry after 44 seconds"))
        tui = ChatTui(lambda: [], lambda _: {}, send)
        tui.input.text = "hello"
        tui.submit_prompt()
        self.wait(tui)
        saved = local_history.active()
        self.assertIn("rate_limit_until", saved["messages"][-1])
        second = ChatTui(lambda: [], lambda _: {}, send, initial_messages=saved["messages"])
        self.assertGreater(second.cooldown_remaining(), 0)
        self.assertIn("retry in", second.render_transcript())
        tui.rate_limit_until = time.time() - 1
        tui.messages[-1]["rate_limit_until"] = time.time() - 1
        self.assertIn("ready to retry", tui.render_transcript())
        tui.submit_prompt()
        self.wait(tui)
        self.assertEqual(send.call_count, 2)
