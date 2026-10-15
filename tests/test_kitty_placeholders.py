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

    def test_inline_is_real_image_cells_and_file_link(self):
        tui = ChatTui(lambda: [],lambda _: {},lambda *args: {})
        tui.native_image_preview = True
        terminal = TTYStub()
        output = self.render(tui, terminal)
        self.assertIn(PLACEHOLDER, output)
        self.assertNotIn("▀", output)
        self.assertIn(self.file.as_uri(), output)
        self.assertIn("/open", output)
        self.assertIn(b"U=1",terminal.buffer.getvalue())

        # prompt_toolkit's ANSI parser preserves the same colored placeholder
        # cells and combining diacritics across TUI repaint.
        pairs = ANSI(output).__pt_formatted_text__()
        text = "".join(part for _,part,*_ in pairs)
        self.assertIn(PLACEHOLDER + DIACRITICS[0] + DIACRITICS[0],text)
        self.assertEqual(text.count(PLACEHOLDER), 72 * 22)
        colors = {style for style,part,*_ in pairs if part == PLACEHOLDER}
        self.assertEqual(len(colors),1)

    def test_redraw_keeps_cells_without_reuploading(self):
        tui = ChatTui(lambda: [],lambda _: {},lambda *args: {})
        tui.native_image_preview = True
        terminal = TTYStub()
        first = self.render(tui, terminal)
        uploaded = len(terminal.buffer.getvalue())
        self.assertGreater(uploaded,100)
        second = self.render(tui, terminal)
        self.assertEqual(first,second)
        self.assertEqual(len(terminal.buffer.getvalue()),uploaded)

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
