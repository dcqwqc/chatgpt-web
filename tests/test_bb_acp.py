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
        model_details = {
            "gpt-6-thinking": {
                "slug": "gpt-6-thinking", "reasoning_type": "reasoning",
                "efforts": [
                    {"value": "medium", "name": "Medium", "thinking_effort": "standard"},
                    {"value": "high", "name": "High", "thinking_effort": "extended"},
                ],
            },
            "gpt-6-instant": {
                "slug": "gpt-6-instant", "reasoning_type": "none", "efforts": [],
            }
        }
        def catalog():
            server.model_cache = model_list
            server.model_capabilities_cache = model_details
            return model_list
        server.catalog = catalog
        server.catalog()
        with tempfile.TemporaryDirectory() as d, patch.object(bb_acp, "ROOT", Path(d)):
            await server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": 1}})
            self.assertEqual(messages[-1]["result"]["protocolVersion"], 1)
            await server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "session/new",
                                   "params": {"cwd": d, "mcpServers": []}})
            sid = messages[-1]["result"]["sessionId"]
            self.assertEqual(messages[-1]["result"]["models"]["currentModelId"],
                             "gpt-6-thinking")
            self.assertEqual([o["value"] for o in
                              messages[-1]["result"]["configOptions"][1]["options"]],
                             ["medium", "high"])
            await server.dispatch({"jsonrpc": "2.0", "id": 20,
                                   "method": "session/set_config_option",
                                   "params": {"sessionId": sid,
                                              "configId": "thinking_effort",
                                              "value": "high"}})
            self.assertEqual(bb_acp.load_session(sid)["reasoning"], "high")
            self.assertEqual(messages[-1]["result"]["configOptions"][1]["currentValue"], "high")
            await server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "session/set_model",
                                   "params": {"sessionId": sid, "modelId": "gpt-6-instant"}})
            self.assertEqual(bb_acp.load_session(sid)["model"], "gpt-6-instant")
            self.assertEqual(bb_acp.load_session(sid)["reasoning"], "none")
            self.assertEqual(messages[-1]["result"]["configOptions"][1]["options"],
                             [{"value": "none", "name": "None"}])
            await server.dispatch({"jsonrpc": "2.0", "id": 21,
                                   "method": "session/set_config_option",
                                   "params": {"sessionId": sid,
                                              "configId": "thinking_effort",
                                              "value": "high"}})
            self.assertIn("error", messages[-1])
            self.assertEqual(bb_acp.load_session(sid)["reasoning"], "none")
            await server.dispatch({"jsonrpc": "2.0", "id": 4, "method": "session/set_model",
                                   "params": {"sessionId": sid, "modelId": "fake-model"}})
            self.assertIn("error", messages[-1])
            self.assertEqual(bb_acp.load_session(sid)["model"], "gpt-6-instant")
            await server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "session/load",
                                   "params": {"sessionId": sid, "cwd": d, "mcpServers": []}})
            self.assertEqual(messages[-1]["result"]["models"]["currentModelId"],
                             "gpt-6-instant")
            self.assertEqual(messages[-1]["result"]["configOptions"][1]["currentValue"],
                             "none")

    def test_prompt_effort_is_passed_only_for_supported_model(self):
        server = bb_acp.Server()
        server.model_capabilities_cache = {
            "gpt-6-thinking": {
                "slug": "gpt-6-thinking", "reasoning_type": "reasoning",
                "efforts": [{"value": "high", "name": "High",
                             "thinking_effort": "extended"}]
            }
        }
        session = {"model": "gpt-6-thinking", "reasoning": "high"}
        self.assertEqual(server.reasoning_options("gpt-6-thinking"),
                         [{"value": "high", "name": "High"}])
        self.assertEqual(server.reasoning_options("gpt-6-instant"),
                         [{"value": "auto", "name": "Automatic"}])

class PromptMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_reasoning_is_passed_to_cli_without_global_mutation(self):
        async def run_case(model, reasoning, expected_effort):
            server = bb_acp.Server()
            sid = "b" * 32
            server.sessions[sid] = {
                "sessionId": sid, "model": model,
                "reasoning": reasoning, "conversation_id": None
            }
            server.model_capabilities_cache = {
                "gpt-6-thinking": {
                    "slug": "gpt-6-thinking", "reasoning_type": "reasoning",
                    "efforts": [
                        {"value": "medium", "thinking_effort": "standard"},
                        {"value": "high", "thinking_effort": "extended"},
                    ]
                },
                "gpt-6-instant": {
                    "slug": "gpt-6-instant", "reasoning_type": "none",
                    "efforts": []
                }
            }
            emitted = []
            async def emit(message):
                emitted.append(message)
            server.emit = emit
            commands = []
            async def fake_exec(*args, **kwargs):
                commands.append(args)
                class FakeProc:
                    def __init__(self):
                        self.stdout = asyncio.StreamReader()
                        self.stdout.feed_data(b'{"type":"result","conversation_id":"mock"}\n')
                        self.stdout.feed_eof()
                        self.stderr = asyncio.StreamReader()
                        self.stderr.feed_eof()
                        self.returncode = 0
                    async def wait(self):
                        return 0
                return FakeProc()
            with tempfile.TemporaryDirectory() as root, patch.object(
                bb_acp, "ROOT", Path(root)
            ), patch.object(
                bb_acp.asyncio, "create_subprocess_exec", side_effect=fake_exec
            ):
                await server.do_prompt(20, sid, [
                    {"type": "text", "text": "Only a fake test prompt"}
                ])
            self.assertEqual(emitted[-1]["result"]["stopReason"], "end_turn")
            argv = list(commands[0])
            self.assertEqual(argv[argv.index("--model")+1], model)
            if expected_effort is None:
                self.assertNotIn("--thinking-effort", argv)
            else:
                self.assertEqual(
                    argv[argv.index("--thinking-effort")+1], expected_effort)
        await run_case("gpt-6-thinking", "high", "extended")
        await run_case("gpt-6-thinking", "medium", "standard")
        await run_case("gpt-6-instant", "none", None)
