"""Portable symlink launcher and executable resolution tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.test_rate_limit import load_client_module

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_symlink_launcher_resolves_source_even_outside_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            temp = Path(folder)
            bin_dir = temp / "bin"
            bin_dir.mkdir()
            fake_uv = bin_dir / "uv"
            fake_uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake_uv.chmod(0o755)
            nested = temp / "nested"
            nested.mkdir()
            (nested / "client").symlink_to(ROOT / "run")
            (bin_dir / "my-chat").symlink_to(nested / "client")
            result = subprocess.run(
                [str(bin_dir / "my-chat"), "status", "--output", "json"],
                cwd=temp, capture_output=True, text=True, timeout=5, check=False,
                env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                ["run", "--script", str(ROOT / "chatgpt-web"),
                 "status", "--output", "json"],
            )

    def test_symlink_chain_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "link-0").symlink_to(ROOT / "run")
            for index in range(1, 22):
                (root / f"link-{index}").symlink_to(f"link-{index - 1}")
            result = subprocess.run(
                [str(root / "link-21")], capture_output=True, text=True,
                timeout=5, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("too many launcher symlinks", result.stderr)

    def test_node_binary_uses_override_or_system_path(self):
        client = load_client_module()
        with patch.dict(os.environ, {"CHATGPT_WEB_NODE_BINARY": "/custom/bin/node"}):
            self.assertEqual(client.node_binary(), "/custom/bin/node")
        with patch.dict(os.environ, {"CHATGPT_WEB_NODE_BINARY": ""}), patch.object(
            client.shutil, "which", return_value="/opt/node/bin/node"
        ):
            self.assertEqual(client.node_binary(), "/opt/node/bin/node")
        with patch.dict(os.environ, {"CHATGPT_WEB_NODE_BINARY": ""}), patch.object(
            client.shutil, "which", return_value=None
        ):
            self.assertEqual(client.node_binary(), "node")


if __name__ == "__main__":
    unittest.main()
