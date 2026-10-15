import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from PIL import Image

from chatgpt_tui import ChatTui


def make_tui():
    return ChatTui(lambda: [], lambda _: {}, lambda *args: {}, get_usage=lambda: {
        "remaining_messages": None,
        "codex": {"windows": {
            "primary_window": {"remaining_percent": 99, "reset_after_seconds": 3600},
            "secondary_window": {"remaining_percent": 80, "reset_after_seconds": 90000},
        }}
    })


class PresentationTests(unittest.TestCase):
    def test_assistant_and_user_not_on_white(self):
        tui = make_tui()
        for role in ("assistant", "user", "system"):
            content = tui.render_message(role, "Hello **world**", 82, False, False)
            self.assertIn("48;2;", content)
            self.assertNotIn("48;2;255;255;255", content)

    def test_inline_preview_in_messages(self):
        tui = make_tui()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "image.png"
            Image.new("RGB", (60, 60), (150, 90, 120)).save(path)
            tui.preview_image_command(str(path))
            self.assertEqual(tui.messages[-1]["role"], "image")
            picture = tui.render_message("image", str(path), 80, False, False)
            self.assertIn("▀", picture)

    def test_missing_preview_does_not_append_message(self):
        tui = make_tui()
        tui.preview_image_command("/not/a/file.png")
        self.assertEqual(tui.messages, [])
        self.assertIn("Cannot preview", tui.status)

    def test_selected_model_always_visible(self):
        tui = make_tui()
        tui.model_filtered = [
            {"slug": f"gpt-{i}", "title": f"Model {i}"} for i in range(20)
        ]
        for index in (0, 4, 7, 10, 15, 19):
            tui.model_index = index
            content = "".join(text for _, text in tui.render_model_list())
            self.assertIn(f"›  gpt-{index}", content)
            self.assertEqual(content.count("\n"), min(7, len(tui.model_filtered)))

    def test_model_steps_during_loading_are_queued(self):
        tui = make_tui()
        self.assertEqual(tui.model_pending_steps, 0)
        tui.model_loading = True
        tui.model_pending_steps += 3
        tui.model_index = 6
        count = 11
        tui.model_index = max(0, min(count - 1, tui.model_index + tui.model_pending_steps))
        self.assertEqual(tui.model_index, 9)

    def test_usage_is_presented_with_separate_quotas(self):
        tui = make_tui()
        tui.display_usage()
        import time
        for _ in range(30):
            if any("Short window" in m["text"] for m in tui.messages):
                break
            time.sleep(0.02)
        all_text = "\n".join(m["text"] for m in tui.messages)
        self.assertIn("ChatGPT Web", all_text)
        self.assertIn("99% remaining", all_text)
        self.assertIn("80% remaining", all_text)
        self.assertIn("separate", all_text)


if __name__ == "__main__":
    unittest.main()
