import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from image_backend import prepare_image_command, generate_image


class ImageBackendTests(unittest.TestCase):
    def test_generate_builds_safe_argument_array(self):
        with patch("image_backend.shutil.which", return_value="/usr/bin/image-use"):
            argv, path = prepare_image_command("generate", "A cat; rm -rf /", backend="codex")
        self.assertEqual(argv[0], "/usr/bin/image-use")
        self.assertIn("A cat; rm -rf /", argv)
        self.assertNotIn("sh", argv)
        self.assertEqual(path.suffix, ".png")

    def test_edit_requires_real_image(self):
        with patch("image_backend.shutil.which", return_value="/usr/bin/image-use"):
            with self.assertRaisesRegex(ValueError, "Image not found"):
                prepare_image_command("edit", "Edit it", reference="/does/not/exist.png")

    def test_animate_uses_subcommand(self):
        with patch("image_backend.shutil.which", return_value="/usr/bin/image-use"):
            argv, path = prepare_image_command("animate", "Walking cat")
        self.assertEqual(argv[1], "animate")
        self.assertEqual(path.suffix, ".webp")

    def test_rejects_invalid_backend_and_extension(self):
        with patch("image_backend.shutil.which", return_value="/usr/bin/image-use"):
            with self.assertRaisesRegex(ValueError, "Invalid backend"):
                prepare_image_command("generate", "cat", backend="foo")
            with self.assertRaisesRegex(ValueError, "Output must"):
                prepare_image_command("generate", "cat", output="/tmp/test.sh")

    def test_success_requires_output(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "result.png"
            with patch("image_backend.shutil.which", return_value="/usr/bin/image-use"), patch("image_backend.subprocess.run", return_value=Mock(returncode=0, stderr="", stdout="")):
                with self.assertRaisesRegex(RuntimeError, "no output image"):
                    generate_image("generate", "cat", output=str(destination))

    def test_run_success_and_no_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "result.png"
            destination.write_bytes(b"PNG fake test")
            with patch("image_backend.shutil.which", return_value="/usr/bin/image-use"), patch("image_backend.subprocess.run", return_value=Mock(returncode=0, stderr="", stdout="")) as runner:
                result = generate_image("generate", "cat", output=str(destination))
            self.assertEqual(result.output, str(destination))
            self.assertFalse(runner.call_args.kwargs.get("shell", False))


if __name__ == "__main__":
    unittest.main()
