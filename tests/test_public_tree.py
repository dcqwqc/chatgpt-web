"""Offline safety tests for the public source publication gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_public_tree.py"
spec = importlib.util.spec_from_file_location("public_tree_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


class PublicTreeAuditTests(unittest.TestCase):
    def test_patch_files_are_scanned_as_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            patchfile = root / "changes.patch"
            patchfile.write_text("endpoint: " + ".".join(("192", "168", "1", "6")))
            self.assertIn("private or CGNAT IP", str(audit.inspect_path(root, "changes.patch")))

    def test_sensitive_files_rejected_even_when_content_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".npmrc").write_text("")
            self.assertIn("forbidden private artifact", str(audit.inspect_path(root, ".npmrc")))

    def test_secret_signatures_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.patch").write_text("token = " + "sk-" + "T" * 30)
            self.assertIn("credential-like content", str(audit.inspect_path(root, "config.patch")))

    def test_symlinks_are_rejected_without_following_private_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "target").write_text("safe")
            (root / "config.ts").symlink_to(root / "target")
            self.assertIn("symbolic link", str(audit.inspect_path(root, "config.ts")))

    def test_binary_and_safe_text_are_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "logo.png").write_bytes(b"\x89PNG\x00fakedata")
            (root / "source.patch").write_text("function f() { return 1; }\n")
            self.assertEqual(audit.inspect_path(root, "logo.png"), [])
            self.assertEqual(audit.inspect_path(root, "source.patch"), [])

    def test_oversize_artifacts_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "large.txt").write_bytes(b"a" * (audit.MAX_TEXT_BYTES + 1))
            self.assertIn("oversize", str(audit.inspect_path(root, "large.txt")))

    def test_main_handles_null_delimited_filenames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "safe script.py").write_text("print(42)")
            names = b"safe script.py\x00"
            with patch.object(audit, "ROOT", root), patch.object(
                audit.subprocess, "check_output", return_value=names
            ):
                self.assertEqual(audit.main(), 0)


if __name__ == "__main__":
    unittest.main()
