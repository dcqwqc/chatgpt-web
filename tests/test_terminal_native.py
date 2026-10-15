from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from terminal_native import supports_native, load_png, geometry, render_native


class NativeImageTests(unittest.TestCase):
    def test_detect_ghostty(self):
        self.assertTrue(supports_native({"TERM": "xterm-ghostty"}))
        self.assertTrue(supports_native({"TERM": "xterm-kitty"}))
        self.assertFalse(supports_native({"TERM": "dumb"}))
        self.assertFalse(supports_native({"TERM": "tmux-256color", "TMUX": "socket"}))

    def test_geometry_fits_without_stretching(self):
        self.assertEqual(geometry(1200, 800, 100, 25), (75, 25))
        self.assertEqual(geometry(800, 1200, 100, 25), (33, 25))
        self.assertEqual(geometry(1000, 1000, 90, 20), (40, 20))

    def test_send_complete_native_png(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/"image.png"
            Image.new("RGB", (400, 240), (110, 180, 80)).save(file)
            output=BytesIO()
            dims=render_native(file,output,65,20)
            data=output.getvalue()
            self.assertEqual(dims,(65,20))
            self.assertIn(b"a=T,f=100,t=d,c=65,r=20,C=1,q=2",data)
            self.assertTrue(data.endswith(bytes([13,10])*20))
            self.assertTrue(b"m=0;" in data)
            self.assertNotIn(b"\x1bPtmux",data)

    def test_tmux_passthrough_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"small.png"
            Image.new("RGBA",(120,100),"red").save(file)
            out=BytesIO()
            render_native(file,out,40,16,tmux_passthrough=True)
            self.assertIn(b"Ptmux;",out.getvalue())

    def test_source_unmodified(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"larger.png"
            Image.new("RGB",(3100,2200),"gold").save(file)
            original=file.read_bytes()
            png,size=load_png(file)
            self.assertLessEqual(max(size),2200)
            self.assertEqual(original,file.read_bytes())
            self.assertTrue(png.startswith(bytes([137,80,78,71])))

    def test_higher_resolution_than_ansi_half_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "detail.png"
            Image.new("RGB", (900, 700), "purple").save(source)
            png, dimensions = load_png(source)
            self.assertEqual(dimensions, (900, 700))
            self.assertTrue(len(png) > 50)
            self.assertGreater(dimensions[0], 42)

    def test_invalid_paths_rejected(self):
        with self.assertRaises(ValueError):
            load_png("/not/a/real.png")


if __name__=="__main__":
    unittest.main()
