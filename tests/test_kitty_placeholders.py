"""Pure protocol and prompt-toolkit rendering regressions for HD inline images."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from prompt_toolkit.formatted_text import ANSI
from wcwidth import wcswidth

from chatgpt_tui import ChatTui
from kitty_placeholders import (
    DIACRITICS, PLACEHOLDER, dimensions, id_to_rgb, image_id_for_file,
    placeholder_rows, virtual_placement_packets, prepare_virtual_image,
)


class TTYStub:
    def __init__(self):
        self.buffer = BytesIO()
    def isatty(self):
        return True
    def write(self, contents):
        return len(contents)
    def flush(self):
        pass


class KittyProtocolTests(unittest.TestCase):
    def test_diacritics_and_cell_width(self):
        self.assertEqual(ord(DIACRITICS[0]), 0x305)
        self.assertEqual(ord(DIACRITICS[1]), 0x30d)
        cells = placeholder_rows(42, 3, 2)
        self.assertEqual(len(cells), 2)
        self.assertEqual(wcswidth(cells[0]), 3)
        self.assertEqual(cells[0][:3], PLACEHOLDER + DIACRITICS[0] + DIACRITICS[0])
        self.assertEqual(cells[1][:3], PLACEHOLDER + DIACRITICS[1] + DIACRITICS[0])

    def test_image_color_encodes_id(self):
        self.assertEqual(id_to_rgb(42), (42, 0, 0))
        self.assertEqual(id_to_rgb(0x010203), (3, 2, 1))

    def test_real_png_virtual_placement_transmits_without_cursor_motion(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "image.png"
            Image.new("RGB", (480, 320), "purple").save(file)
            ident, cols, rows, packets = prepare_virtual_image(file, 100)
            self.assertEqual((cols,rows), (72,24))
            self.assertIn(b"U=1", packets[0])
            self.assertIn(b"f=100", packets[0])
            self.assertIn(f"i={ident}".encode(), packets[0])
            self.assertIn(b"a=T", packets[0])
            self.assertNotIn(b"C=1", packets[0])
            self.assertTrue(packets[-1].endswith(bytes((27,92))))
            self.assertIn(b"m=0", packets[-1])

    def test_image_id_stable_per_file(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "x.png"
            Image.new("RGB", (10, 10), "orange").save(file)
            self.assertEqual(image_id_for_file(file), image_id_for_file(file))
            self.assertGreater(image_id_for_file(file), 0)


class NativeInlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.file = Path(self.temp.name) / "generated art.png"
        Image.new("RGB", (160,100), "purple").save(self.file)

    def tearDown(self):
        self.temp.cleanup()

    def render(self, tui, terminal):
        with patch("chatgpt_tui.sys.stdout", terminal):
            return tui.render_message("image", str(self.file), 92, False, False)

    def test_native_image_uses_direct_pixel_packets_in_scrollback(self):
        tui = ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        tui.native_image_preview = True
        terminal = TTYStub()
        with patch("chatgpt_tui.sys.stdout", terminal):
            tui.write_complete_messages([{"role":"image", "text":str(self.file)}])
        data = terminal.buffer.getvalue()
        self.assertIn(b"a=T,f=100", data)
        self.assertNotIn(b"U=1", data)
        self.assertIn("Saved original:", tui.render_message("image", str(self.file), 90, False, False))
        self.assertIn(self.file.as_uri(), tui.render_message("image", str(self.file), 90, False, False))

    def test_live_image_is_non_blank_placeholder_with_saved_path(self):
        tui = ChatTui(lambda: [], lambda _: {}, lambda *args: {})
        tui.native_image_preview = True
        terminal = TTYStub()
        output = self.render(tui, terminal)
        self.assertNotIn(PLACEHOLDER, output)
        self.assertIn("Image ready", output)
        self.assertIn(self.file.as_uri(), output)
        self.assertEqual(len(terminal.buffer.getvalue()), 0)

    def test_terminal_reload_resends_virtual_placement(self):
        tui = ChatTui(lambda: [],lambda _: {},lambda *args: {})
        tui.native_image_preview = True
        terminal = TTYStub()
        self.render(tui, terminal)
        before = len(terminal.buffer.getvalue())
        with patch("chatgpt_tui.sys.stdout",terminal):
            tui.reload_virtual_placements([{"role":"image","text":str(self.file)}])
        self.assertGreater(len(terminal.buffer.getvalue()), before)

    def test_fallback_still_shows_saved_path_and_open_command(self):
        tui = ChatTui(lambda: [],lambda _: {},lambda *args: {})
        tui.native_image_preview = False
        text = self.render(tui,TTYStub())
        self.assertIn("▀",text)
        self.assertIn(self.file.as_uri(),text)
        self.assertIn("/open",text)


if __name__ == "__main__":
    unittest.main()
