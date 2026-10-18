import unittest
from unittest.mock import patch

try:
    from prompt_toolkit.data_structures import Size
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

    def test_picker_lists_can_shrink_to_fit(self):
        tui = self.make_tui()
        for window in (tui.resume_list_window, tui.model_list_window):
            self.assertEqual(window.height.min, 1)
            self.assertEqual(window.height.preferred, 7)
            self.assertEqual(window.height.max, 7)
        self.assertEqual(tui.command_hints_window.height.min, 1)
        self.assertEqual(tui.command_hints_window.height.preferred, 5)

    def test_compact_layout_uses_available_viewport_rows(self):
        tui = self.make_tui()
        tui.native_image_preview = False
        with patch.object(tui.app.output, "get_size", return_value=Size(rows=9, columns=80)):
            self.assertEqual(tui.viewport_height().preferred, 9)
            self.assertTrue(tui.full_layout_filter())
            self.assertTrue(tui.picker_details_filter())
            tui.resume_visible = True
            self.assertFalse(tui.full_layout_filter())
            self.assertTrue(tui.resume_filter())

    def test_tiny_viewport_does_not_force_six_rows_or_show_picker(self):
        tui = self.make_tui()
        tui.native_image_preview = False
        tui.input.text = "/model"
        with patch.object(tui.app.output, "get_size", return_value=Size(rows=2, columns=40)):
            self.assertEqual(tui.viewport_height().preferred, 2)
            self.assertFalse(tui.commands_filter())
            tui.resume_visible = True
            self.assertFalse(tui.resume_filter())

    def test_picker_shortcuts_do_not_focus_hidden_controls_on_tiny_viewport(self):
        tui = self.make_tui()
        tui.native_image_preview = False
        with patch.object(tui.app.output, "get_size", return_value=Size(rows=2, columns=40)):
            tui.open_resume()
            self.assertFalse(tui.resume_visible)
            tui.open_models()
            self.assertFalse(tui.model_visible)

    def test_compact_layout_never_renders_window_too_small_placeholder(self):
        import asyncio
        from prompt_toolkit.application.current import set_app
        from prompt_toolkit.output import DummyOutput

        class SizedDummyOutput(DummyOutput):
            def __init__(self, rows, columns):
                self.size = Size(rows=rows, columns=columns)

            def get_size(self):
                return self.size

        cases = [
            (2, 20, "resume", False),
            (3, 30, "resume", False),
            (6, 40, "model", False),
            (9, 60, "resume", False),
            (9, 60, "model", False),
            (9, 60, "commands", False),
            (12, 80, "model", False),
            (16, 80, "model", False),
            (24, 80, "model", True),
        ]

        async def render_case(rows, columns, panel, native_images):
            tui = self.make_tui()
            tui.native_image_preview = native_images
            if panel == "resume":
                tui.resume_visible = True
            elif panel == "model":
                tui.model_visible = True
            else:
                tui.input.text = "/model"
            output = SizedDummyOutput(rows, columns)
            tui.app.output = output
            tui.app.renderer.output = output
            with set_app(tui.app):
                tui.app.renderer.render(tui.app, tui.app.layout)
                await asyncio.sleep(0)
            screen = tui.app.renderer._last_screen
            rendered = "\n".join(
                "".join(cell.char for _, cell in sorted(screen.data_buffer.get(row, {}).items()))
                for row in range(rows)
            )
            self.assertNotIn(
                "Window too small", rendered,
                f"placeholder rendered at {rows}x{columns}, panel={panel}, native_images={native_images}",
            )

        async def render_all_cases():
            for case in cases:
                with self.subTest(case=case):
                    await render_case(*case)

        asyncio.run(render_all_cases())

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
