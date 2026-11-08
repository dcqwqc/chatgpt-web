"""Accepted ChatGPT SSE errors use GET-only recovery without duplicate POST."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

import bb_acp


CID = "6af03ffb-8920-83eb-a2cc-8ceaf959bf9c"
MID = "accepted-source-message"
PROMPT = "Continue existing work after stream failure"


class AcceptedStreamErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    async def run_case(self, *, accepted: bool):
        server = bb_acp.Server()
        server.variants = {"gpt-6":{"thinking":"gpt-6-thinking"}}
        server.model_capabilities_cache = {"gpt-6-thinking":{"efforts":[]}}
        session = {
            "sessionId": "a"*32, "cwd": str(Path(self.folder.name)/"thr_abcdef123"),
            "conversation_id": CID, "model":"gpt-6", "mode":"thinking",
            "adapter_policy_hash": "test-hash",
        }
        server.sessions[session["sessionId"]] = session
        events = []
        async def emit(event):
            events.append(event)
        server.emit = emit

        class Stream:
            def __init__(self):
                self.stdout = asyncio.StreamReader()
                if accepted:
                    for obj in (
                        {"type":"submission_identity","message_id":MID},
                        {"type":"stream_conversation_id","conversation_id":CID},
                        {"type":"phase","status":"Generating answer","submitted":True},
                    ):
                        self.stdout.feed_data((json.dumps(obj)+"\n").encode())
                self.stdout.feed_eof()
                self.stderr = asyncio.StreamReader()
                self.stderr.feed_data(b"chatgpt-web: peer closed connection (incomplete chunked read)")
                self.stderr.feed_eof()
                self.returncode = 1
            async def wait(self):
                return 1

        spawns = []
        async def start_process(*args,**kwargs):
            spawns.append(args)
            return Stream()

        recovery = AsyncMock()
        with patch.object(bb_acp, "ROOT",Path(self.folder.name)),              patch.object(bb_acp, "adapter_policy",return_value=("", "test-hash")),              patch.object(bb_acp, "generated_media",return_value=[]),              patch.object(bb_acp.asyncio,"create_subprocess_exec",side_effect=start_process),              patch.object(server,"recover_accepted_turn",recovery):
            if accepted:
                await server.do_prompt(44,session["sessionId"],
                                       [{"type":"text","text":PROMPT}])
            else:
                with self.assertRaisesRegex(RuntimeError,"incomplete chunked"):
                    await server.do_prompt(44,session["sessionId"],
                                           [{"type":"text","text":PROMPT}])
            saved=bb_acp.load_session(session["sessionId"]) if accepted else session
        return spawns,recovery,saved

    async def test_accepted_stream_error_enters_recovery_without_second_submission(self):
        spawns,recovery,saved=await self.run_case(accepted=True)
        self.assertEqual(len(spawns),1)
        recovery.assert_awaited_once()
        args=recovery.await_args.args
        self.assertEqual(args[-1],PROMPT)
        self.assertEqual(args[-2]["message_id"],MID)
        self.assertEqual(args[-2]["conversation_id"],CID)
        self.assertTrue(saved["pending_turn"]["accepted"])
        self.assertEqual(saved["pending_turn"]["prompt_sha256"],
                         hashlib.sha256(PROMPT.encode()).hexdigest())

    async def test_failure_before_acceptance_fails_closed(self):
        spawns,recovery,saved=await self.run_case(accepted=False)
        self.assertEqual(len(spawns),1)
        recovery.assert_not_awaited()
        self.assertNotIn("pending_turn",saved)


if __name__=="__main__":
    unittest.main()
