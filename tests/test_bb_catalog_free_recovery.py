"""Accepted-message reattachment must not depend on model catalog availability."""
import asyncio
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest

import bb_acp


class NoCatalogRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.root = patch.object(bb_acp, "ROOT", self.path)
        self.root.start()
        self.addCleanup(self.root.stop)
        self.server = bb_acp.Server()
        self.sent = []
        async def emit(msg):
            self.sent.append(msg)
        self.server.emit = emit
        self.prompt = "My accepted original request"
        self.pending = {
            "accepted": True, "conversation_id": "6af03ffb-8920-83eb-a2cc-8ceaf959bf9c",
            "message_id": "confirmed-original",
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
        }
        self.cwd = str(self.path / "thr_sb9t3b8szp")
        self.session = {
            "sessionId": "a"*32, "cwd": self.cwd,
            "conversation_id": self.pending["conversation_id"],
            "model": "gpt-6", "mode": "thinking",
            "pending_turn": self.pending,
        }
        bb_acp.save_session(self.session)

    async def test_new_session_recovers_without_model_catalog_network(self):
        with patch.object(bb_acp, "model_capabilities",
                          side_effect=RuntimeError("429 rate limit")) as network:
            await self.server.dispatch({"jsonrpc":"2.0","id":1,"method":"session/new",
                                        "params":{"cwd":self.cwd}})
        network.assert_not_called()
        response=self.sent[-1]["result"]
        self.assertEqual(response["models"]["currentModelId"],"gpt-6")
        self.assertEqual([v["value"] for v in response["configOptions"][1]["options"]],
                         ["thinking"])
        loaded=bb_acp.load_session(response["sessionId"])
        self.assertEqual(loaded["pending_turn"],self.pending)

    async def test_load_recovery_skips_catalog_even_when_unavailable(self):
        with patch.object(bb_acp, "model_capabilities",
                          side_effect=RuntimeError("429 rate limit")) as network:
            await self.server.dispatch({"jsonrpc":"2.0","id":2,"method":"session/load",
                                        "params":{"sessionId":self.session["sessionId"]}})
        network.assert_not_called()
        self.assertIn("result",self.sent[-1])
        self.assertEqual(self.sent[-1]["result"]["models"]["currentModelId"],"gpt-6")

    async def test_cannot_change_model_until_accepted_answer_restored(self):
        with patch.object(bb_acp, "model_capabilities",
                          side_effect=RuntimeError("429 rate limit")) as network:
            self.server.sessions[self.session["sessionId"]]=self.session
            await self.server.dispatch({"jsonrpc":"2.0","id":3,
                                        "method":"session/set_config_option",
                                        "params":{"sessionId":self.session["sessionId"],
                                                  "configId":"model","value":"gpt-5-6"}})
        network.assert_not_called()
        self.assertIn("locked",self.sent[-1]["error"]["message"].lower())


if __name__ == "__main__":
    unittest.main()
