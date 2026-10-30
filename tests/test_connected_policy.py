"""Regression coverage for connected tools, legacy chats and BB rendering."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import bb_acp
from test_bb_acp import source_catalog


class ConnectedPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = patch.object(bb_acp, "ROOT", Path(self.temp.name))
        self.root.start()
        self.catalog = patch.object(bb_acp, "model_capabilities", return_value=source_catalog())
        self.catalog.start()
        self.server = bb_acp.Server()
        self.server.catalog()
        self.records = []
        async def emit(record):
            self.records.append(record)
        self.server.emit = emit
        self.sid = "d" * 32
        self.session = {"sessionId": self.sid, "conversation_id": "existing-chat",
                        "model": "gpt-6", "mode": "thinking", "cwd": "/tmp/workspace"}
        self.server.sessions[self.sid] = self.session
        self.prompts = []

    async def asyncTearDown(self):
        self.catalog.stop()
        self.root.stop()
        self.temp.cleanup()

    async def send(self, fail=False, messages=()):
        async def fake_exec(*argv, **kwargs):
            self.prompts.append(argv[-1])
            class Process:
                def __init__(self):
                    self.returncode = 1 if fail else 0
                    self.stdout = asyncio.StreamReader()
                    if not fail:
                        for text in messages:
                            self.stdout.feed_data((json.dumps({"type": "message",
                                "role": "assistant", "text": text}) + "\n").encode())
                        self.stdout.feed_data((json.dumps({"type": "result",
                            "conversation_id": "existing-chat"}) + "\n").encode())
                    self.stdout.feed_eof()
                    self.stderr = asyncio.StreamReader()
                    if fail:
                        self.stderr.feed_data(b"connector unavailable")
                    self.stderr.feed_eof()
                async def wait(self):
                    return self.returncode
            return Process()
        with patch.object(bb_acp.asyncio, "create_subprocess_exec", side_effect=fake_exec):
            await self.server.do_prompt(1, self.sid, [{"type": "text", "text": "Do the authorized work"}])

    async def test_legacy_chat_is_corrected_once_and_policy_changes_refresh(self):
        await self.send()
        self.assertIn("replaces any earlier adapter notice", self.prompts[-1])
        self.assertIn("connected MCP apps", self.prompts[-1])
        self.assertIn('"cwd": "/tmp/workspace"', self.prompts[-1])
        self.assertNotIn("Offer guidance or code as text only", self.prompts[-1])
        self.assertEqual(self.session["conversation_id"], "existing-chat")
        self.assertIn("adapter_policy_hash", bb_acp.load_session(self.sid))
        await self.send()
        self.assertEqual(self.prompts[-1], "Do the authorized work")
        with patch.object(bb_acp, "load_bb_core_policy", return_value="Updated published policy"):
            await self.send()
        self.assertIn("Updated published policy", self.prompts[-1])

    async def test_failed_send_does_not_mark_policy_delivered(self):
        with self.assertRaisesRegex(RuntimeError, "connector unavailable"):
            await self.send(fail=True)
        self.assertNotIn("adapter_policy_hash", self.session)
        await self.send()
        self.assertIn("capability and execution contract v2", self.prompts[-1])

    async def test_reload_updates_workspace_without_losing_chat_or_model(self):
        bb_acp.save_session(self.session)
        await self.server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "session/load",
            "params": {"sessionId": self.sid, "cwd": "/tmp/next-workspace", "mcpServers": []}})
        self.session = self.server.sessions[self.sid]
        self.assertEqual(self.session["cwd"], "/tmp/next-workspace")
        self.assertEqual(self.session["conversation_id"], "existing-chat")
        self.assertEqual(self.session["mode"], "thinking")
        await self.send()
        self.assertIn('"cwd": "/tmp/next-workspace"', self.prompts[-1])

    async def test_rich_markup_is_readable_but_code_is_preserved(self):
        source = '<row><badge color="warning">Blocked</badge><text>Try another route.</text></row>'
        await self.server.update(self.sid, source)
        text = self.records[-1]["params"]["update"]["content"]["text"]
        self.assertNotIn("<row>", text)
        self.assertIn("**Blocked**", text)
        code = "```html\n<row>literal sample</row>\n```"
        await self.server.update(self.sid, code)
        self.assertEqual(self.records[-1]["params"]["update"]["content"]["text"], code)

    async def test_empty_tool_messages_do_not_become_blank_lines(self):
        await self.send(messages=["Checked the host.", "", "", "  ", "Actual answer."])
        answer = "".join(r["params"]["update"]["content"]["text"] for r in self.records
            if r.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk")
        self.assertEqual(answer, "Checked the host.\nActual answer.")
