"""Local, network-free BB/ChatGPT Web ACP adapter regression tests."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bb_acp


class ModelAndSessionTests(unittest.TestCase):
    def test_live_catalog_filters_internal_gates_and_disambiguates(self):
        catalog = {"models": [
            {"slug": "gpt-6", "title": "GPT-6"},
            {"slug": "gpt-6-thinking", "title": "GPT-6"},
            {"slug": "chatgpt_alpha_model_external_access_reserved_gate_12",
             "title": "Internal"},
            {"slug": "gpt-6", "title": "Duplicate"},
        ]}
        with patch.object(bb_acp, "daemon_request", return_value=catalog):
            result = bb_acp.models()
        self.assertEqual([r["modelId"] for r in result], ["gpt-6", "gpt-6-thinking"])
        self.assertNotEqual(result[0]["name"], result[1]["name"])

    def test_sessions_persist_privately(self):
        with tempfile.TemporaryDirectory() as d, patch.object(bb_acp, "ROOT", Path(d)):
            state = {"sessionId": "a" * 32, "model": "gpt-6-thinking", "conversation_id": None}
            bb_acp.save_session(state)
            self.assertEqual(bb_acp.load_session("a"*32), state)
            self.assertEqual(bb_acp.session_path("a"*32).stat().st_mode & 0o777, 0o600)
            self.assertRaises(ValueError, bb_acp.session_path, "../../wrong")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_v1_handshake_and_model_switch(self):
        server = bb_acp.Server()
        messages = []
        async def emit(obj):
            messages.append(obj)
        server.emit = emit
        model_list = [{"modelId": "gpt-6-thinking", "name": "Thinking"},
                      {"modelId": "gpt-6-instant", "name": "Instant"}]
        server.catalog = lambda: model_list
        server.model_cache = model_list
        with tempfile.TemporaryDirectory() as d, patch.object(bb_acp, "ROOT", Path(d)):
            await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": 1}})
            self.assertEqual(messages[-1]["result"]["protocolVersion"], 1)
            await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "session/new",
                                   "params": {"cwd": d, "mcpServers": []}})
            sid = messages[-1]["result"]["sessionId"]
            self.assertEqual(messages[-1]["result"]["models"]["currentModelId"],
                             "gpt-6-thinking")
            await server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "session/set_model",
                                   "params": {"sessionId": sid, "modelId": "gpt-6-instant"}})
            self.assertEqual(bb_acp.load_session(sid)["model"], "gpt-6-instant")
            await server.dispatch({"jsonrpc": "2.0", "id": 4, "method": "session/set_model",
                                   "params": {"sessionId": sid, "modelId": "fake-model"}})
            self.assertIn("error", messages[-1])
            self.assertEqual(bb_acp.load_session(sid)["model"], "gpt-6-instant")
            await server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "session/load",
                                   "params": {"sessionId": sid, "cwd": d, "mcpServers": []}})
            self.assertEqual(messages[-1]["result"]["models"]["currentModelId"],
                             "gpt-6-instant")
