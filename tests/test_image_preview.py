import tempfile
from pathlib import Path
import unittest
from PIL import Image

from image_preview import render_preview


class ImagePreviewTests(unittest.TestCase):
    def test_preview_has_color_and_half_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.png"
            image = Image.new("RGB", (120, 80))
            for x in range(120):
                for y in range(80):
                    image.putpixel((x, y), (x * 2, y * 3, 140))
            image.save(path)
            preview = render_preview(path, max_columns=24, max_rows=10)
        self.assertIn("▀", preview)
        self.assertIn("\x1b[38;2;", preview)
        self.assertIn("\x1b[48;2;", preview)
        self.assertIn("\x1b[0m", preview)
        self.assertLessEqual(preview.count("\n") + 1, 10)
        self.assertLessEqual(preview.count("▀"), 240)

    def test_gif_preview_first_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "animated.gif"
            frames = [
                Image.new("RGB", (20, 20), (220, 50, 40)),
                Image.new("RGB", (20, 20), (20, 180, 100)),
            ]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
            preview = render_preview(path, max_columns=12, max_rows=6)
        self.assertIn("▀", preview)

    def test_missing_file_is_handled(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            render_preview("/missing/user/test-image.png")

    def test_large_image_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "oversized.png"
            with path.open("wb") as f:
                f.truncate(45_000_001)
            with self.assertRaisesRegex(ValueError, "45 MB"):
                render_preview(path)


if __name__ == "__main__":
    unittest.main()
