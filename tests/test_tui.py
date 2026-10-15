import unittest

try:
    from prompt_toolkit.layout import HSplit
    from chatgpt_tui import ChatTui
except ImportError:
    ChatTui = None


@unittest.skipIf(ChatTui is None, "TUI dependencies unavailable")
class TuiTests(unittest.TestCase):
    def make_tui(self):
        return ChatTui(
            lambda: [{"id": "one", "title": "Sample conversation"}],
            lambda _: {"id": "one", "title": "Sample conversation", "messages": []},
            lambda *args: {},
        )

    def test_picker_is_inline_not_fullscreen_float(self):
        tui = self.make_tui()
        self.assertIsInstance(tui.app.layout.container, HSplit)
        self.assertEqual(tui.resume_list_window.height.preferred, 7)

    def test_model_panel_is_inline(self):
        tui = self.make_tui()
        self.assertIsInstance(tui.app.layout.container, HSplit)
        self.assertEqual(tui.model_list_window.height.preferred, 7)

    def test_model_selector_filters_by_slug_and_title(self):
        tui = self.make_tui()
        tui.model_items = [
            {"slug":"gpt-6", "title":"GPT-6"},
            {"slug":"gpt-5-6-thinking", "title":"GPT-5.6 Sol"},
        ]
        tui.model_search.text = "thinking"
        tui.filter_models()
        self.assertEqual([item["slug"] for item in tui.model_filtered], ["gpt-5-6-thinking"])

    def test_usage_command_is_exposed(self):
        from chatgpt_tui import SlashCommandCompleter
        names = [item[0] for item in SlashCommandCompleter.COMMANDS]
        self.assertIn("/model", names)
        self.assertIn("/usage", names)

    def test_commands_show_image_actions(self):
        tui = self.make_tui()
        tui.input.text = "/im"
        text = "".join(piece for _, piece in tui.render_command_hints())
        self.assertIn("/image", text)
        self.assertNotIn("/animate", text)

    def test_resume_truncates_titles(self):
        tui = self.make_tui()
        tui.resume_items = [{"id": "one", "title": "Very long title " * 20}]
        tui.filter_resume()
        labels = "".join(piece for _, piece in tui.render_resume_list())
        self.assertLess(len(labels), 100)

    def test_empty_image_does_not_launch_subprocess(self):
        tui = self.make_tui()
        tui.start_image_command("/image", "")
        self.assertIn("Usage", tui.status)
        self.assertFalse(tui.image_busy)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(ChatTui is None, "TUI dependencies unavailable")
class PickerFailureTests(unittest.TestCase):
    def test_failed_fetch_is_not_reported_as_empty_results(self):
        tui = ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        tui.resume_error = "rate limit reached"
        text = "".join(part for _, part in tui.render_resume_list())
        self.assertIn("rate limit", text)
        self.assertNotIn("No matching", text)
