"""BB adapter emits mutable progress rows while keeping one canonical answer."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bb_acp


class LiveActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_phases_commentary_and_tool_calls_are_live_and_replaceable(self):
        events = [
            {"type": "phase", "status": "Checking CLI Project…"},
            {"type": "phase", "status": "Waiting for ChatGPT response…", "submitted": True},
            {"type": "commentary", "id": "c1", "text": "Looking into it"},
            {"type": "commentary", "id": "c1", "text": "Looking into the request"},
            {"type": "tool_start", "id": "t1", "name": "functions.exec", "arguments": ""},
            {"type": "tool_update", "id": "t1", "name": "functions.exec", "arguments": "command=git status"},
            {"type": "tool_end", "id": "t1", "status": "completed"},
            {"type": "delta", "text": "Old answer"},
            {"type": "replace", "text": "Corrected answer"},
            {"type": "message", "role": "assistant", "channel": "commentary",
             "text": "Looking into the request"},
            {"type": "message", "role": "assistant", "channel": "final",
             "text": "Corrected answer."},
            {"type": "result", "conversation_id": "existing"},
        ]
        output = []
        class FakeProcess:
            def __init__(self):
                self.stdout = asyncio.StreamReader()
                self.stdout.feed_data(("\n".join(map(json.dumps, events)) + "\n").encode())
                self.stdout.feed_eof()
                self.stderr = asyncio.StreamReader()
                self.stderr.feed_eof()
                self.returncode = 0
            async def wait(self): return 0
        async def fake_exec(*args, **kwargs): return FakeProcess()
        async def capture(value): output.append(value)
        with tempfile.TemporaryDirectory() as root, patch.object(bb_acp, "ROOT", Path(root)), patch.object(
            bb_acp, "adapter_policy", return_value=("policy", "hash")
        ), patch.object(bb_acp.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            server = bb_acp.Server()
            server.emit = capture
            server.variants = {"gpt-6": {"instant": "gpt-6-instant"}}
            server.model_capabilities_cache = {}
            sid = "e" * 32
            server.sessions[sid] = {"sessionId": sid, "model": "gpt-6",
                                    "mode": "instant", "conversation_id": "existing"}
            await server.do_prompt(123, sid, [{"type": "text", "text": "test"}])
        updates = [x["params"]["update"] for x in output if x.get("method") == "session/update"]
        responses = [x["content"]["text"] for x in updates if x["sessionUpdate"] == "agent_message_chunk"]
        self.assertEqual(responses, ["\n\nLooking into it", "\n\nLooking into the request\n\n", "\n\nCorrected answer."])
        phases = [x for x in updates if x.get("toolCallId", "").endswith("-request")]
        self.assertEqual([x["sessionUpdate"] for x in phases], ["tool_call", "tool_call_update", "tool_call_update"])
        self.assertEqual(phases[-1]["status"], "completed")
        tool_starts = [x for x in updates if x.get("title", "").startswith("Run connected tools")]
        self.assertEqual(len(tool_starts), 3)
        self.assertEqual(tool_starts[0]["sessionUpdate"], "tool_call")
        self.assertEqual(tool_starts[1]["title"], "Run connected tools")
        self.assertNotIn("content", tool_starts[1], "Tool input must not be mislabeled as output")
        self.assertEqual(tool_starts[1]["rawInput"], {"input": "command=git status"})
        self.assertEqual(tool_starts[1]["name"], "Run connected tools")
        self.assertEqual(tool_starts[-1]["status"], "completed")
        previews = [x for x in updates if x.get("toolCallId", "").endswith("-preview")]
        self.assertEqual([x["sessionUpdate"] for x in previews],
                         ["tool_call", "tool_call_update", "tool_call_update"])
        self.assertIn("Old answer", previews[0]["title"])
        self.assertIn("Corrected answer", previews[1]["title"])
        self.assertEqual(previews[-1]["status"], "completed")
        commentary = [x for x in updates if x.get("sessionUpdate") == "agent_message_chunk"
                      and "Looking into" in x["content"]["text"]]
        self.assertEqual(len(commentary), 2)
        self.assertTrue(all("toolCallId" not in x for x in commentary))
        self.assertEqual(output[-1]["result"]["stopReason"], "end_turn")

if __name__ == "__main__": unittest.main()
