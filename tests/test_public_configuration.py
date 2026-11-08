"""Release safety checks without a browser, account or network."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bb_acp
from ops import login_native


class ReleaseConfigTests(unittest.TestCase):
    def test_unknown_public_model_is_listed_without_invented_modes(self):
        catalog = [{"slug": "next-model", "title": "Next Model"},
                   {"slug": "internal", "enable_infer": False},
                   {"slug": "chatgpt_alpha_private", "title": "Private"}]
        self.assertEqual(bb_acp.build_families(catalog),
                         {"next-model": {"default": "next-model"}})
        self.assertEqual(bb_acp.model_rows(catalog),
                         [{"modelId": "next-model", "name": "Next Model"}])

    def test_media_url_requires_trusted_relative_path(self):
        asset = "file_00000000fb3c81f4aa4a0d784f93834b"
        for endpoint in ("", "https://example.com/host", "/path?injection=bad"):
            self.assertNotIn("![Generated image]", bb_acp.image_markdown(asset, endpoint))
        self.assertIn(f"/images?asset={asset}", bb_acp.image_markdown(asset, "/images"))

    def test_login_uses_configured_browser_and_xdg_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "browser"
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            with patch.dict(os.environ, {
                "CHATGPT_WEB_BROWSER_EXECUTABLE": str(executable),
                "XDG_DATA_HOME": folder,
            }):
                self.assertEqual(login_native.chrome_executable(), str(executable))
                self.assertEqual(login_native.browser_profile(),
                                 Path(folder) / "chatgpt-web/browser-profile")

    def test_bad_browser_path_cannot_be_executed(self):
        with patch.dict(os.environ, {
            "CHATGPT_WEB_BROWSER_EXECUTABLE": "/nonexistent/browser",
        }):
            with self.assertRaisesRegex(RuntimeError, "missing"):
                login_native.chrome_executable()

    def test_custom_project_name_appears_in_validation_error(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import spec_from_loader, module_from_spec
        script = Path(__file__).resolve().parents[1] / "chatgpt-web"
        loader = SourceFileLoader("custom_project_error_test", str(script))
        specification = spec_from_loader(loader.name, loader)
        module = module_from_spec(specification)
        loader.exec_module(module)
        client = object.__new__(module.ChatGPTWebClient)
        client.target_project_id = lambda: "g-p-" + "a" * 32
        client.get_conversation = lambda cid: {"id": cid, "gizmo_id": None}
        with patch.object(module, "PROJECT_NAME", "Research"):
            with self.assertRaisesRegex(module.ChatGPTWebError, "outside the Research Project"):
                client.send_prompt("existing-chat", "Hello")

    def test_image_worker_requires_explicit_config(self):
        with patch.object(bb_acp, "IMAGE_BRIDGE", ""):
            import asyncio
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                asyncio.run(bb_acp.image_bridge_call("generate", prompt="test"))


if __name__ == "__main__":
    unittest.main()
