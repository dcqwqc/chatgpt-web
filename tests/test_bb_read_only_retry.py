"""Accepted failures resume by verified GET, never by a second ChatGPT POST."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bb_acp


class MockShow:
    def __init__(self, document):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(json.dumps(document).encode())
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode = 0
    async def communicate(self):
        return await self.stdout.read(), await self.stderr.read()


class ReadOnlyRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_reads_original_then_emits_one_final(self):
        user_prompt = "Exactly my prompt"
        conversation_id = "6af03ffb-8920-83eb-a2cc-8ceaf959bf9c"
        doc = {"id": conversation_id, "messages": [
            {"role": "user", "id": "unique-id", "status": "finished_successfully",
             "parts": ["header\nExactly my prompt"]},
            {"role": "assistant", "channel": "commentary", "status": "finished_successfully",
             "text": "Investigating"},
            {"role": "assistant", "channel": "final", "status": "finished_successfully",
             "recipient": "all", "text": "Original answer."}]}
        calls = []
        responses = []
        async def fake_exec(*argv, **kwargs):
            calls.append(argv)
            return MockShow(doc)
        async def capture(message):
            responses.append(message)
        with tempfile.TemporaryDirectory() as root, patch.object(
            bb_acp, "ROOT", Path(root)
        ), patch.object(bb_acp.asyncio, "create_subprocess_exec", side_effect=fake_exec), patch.object(
            bb_acp, "adapter_policy", return_value=("policy", "hash")
        ):
            server = bb_acp.Server()
            server.emit = capture
            sid = "a" * 32
            server.sessions[sid] = {
                "sessionId": sid, "conversation_id": None, "model": "gpt-6",
                "mode": "thinking", "pending_turn": {
                    "accepted": True,
                    "prompt_sha256": hashlib.sha256(user_prompt.encode()).hexdigest(),
                    "message_id": "unique-id",
                    "conversation_id": conversation_id
                }
            }
            await server.do_prompt(10, sid, [{"type": "text", "text": user_prompt}])
            restored = bb_acp.load_session(sid)
        self.assertEqual(restored["conversation_id"], conversation_id)
        self.assertNotIn("pending_turn", restored)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:3], ("show", conversation_id))
        texts = [e["params"]["update"]["content"]["text"]
                 for e in responses if e.get("method") == "session/update"
                 and e["params"]["update"]["sessionUpdate"] == "agent_message_chunk"]
        self.assertEqual(texts, ["Original answer."])
        self.assertEqual(responses[-1]["result"], {"stopReason": "end_turn"})

    async def test_different_prompt_fails_closed_without_send(self):
        sid = "b" * 32
        server = bb_acp.Server()
        server.sessions[sid] = {
            "sessionId": sid, "conversation_id": None,
            "pending_turn": {"accepted": True, "prompt_sha256":
                             hashlib.sha256(b"first").hexdigest()},
        }
        with patch.object(bb_acp.asyncio, "create_subprocess_exec") as exec_spy:
            with self.assertRaisesRegex(RuntimeError, "No prompt was submitted"):
                await server.do_prompt(10, sid, [{"type": "text", "text": "second"}])
            exec_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
