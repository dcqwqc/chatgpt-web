"""BB native model/mode picker and CLI routing integration, no external calls."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import bb_acp

def source_catalog():
    models = []
    for family, variants in bb_acp.VARIANT_SUFFIXES.items():
        for mode, slug in variants.items():
            kind = "auto" if mode == "auto" else "none" if mode == "instant" else "reasoning"
            efforts = []
            if mode == "thinking" and family in ("gpt-6", "gpt-5-6"):
                efforts = [{"thinking_effort":"standard", "value":"medium"},
                           {"thinking_effort":"extended", "value":"high"}]
            models.append({"slug":slug, "title":family,
                           "reasoning_type":kind, "efforts":efforts})
    models.append({"slug":"chatgpt_alpha_internal","title":"Internal","efforts":[]})
    return models

class BbInstructionEnvelopeTests(unittest.TestCase):
    def test_only_the_bb_prefix_is_removed(self):
        s = "<system_instructions>bb generated tool instructions</system_instructions> Hi!"
        self.assertEqual(bb_acp.remove_bb_system_envelope(s), "Hi!")
        self.assertEqual(bb_acp.remove_bb_system_envelope(
            "What does <system_instructions> mean?"),
            "What does <system_instructions> mean?")
        self.assertEqual(bb_acp.remove_bb_system_envelope(
            "<system_instructions>Unclosed instructions"),
            "<system_instructions>Unclosed instructions")
        self.assertEqual(bb_acp.remove_bb_system_envelope(
            "  <system_instructions>instructions</system_instructions>\nuser text"),
            "user text")

class FamilyTests(unittest.TestCase):
    def test_four_families_only_available_variants_and_no_fabricated_auto(self):
        x=bb_acp.build_families(source_catalog())
        self.assertEqual(len(x),4)
        self.assertEqual(x["gpt-6"],{
            "auto":"gpt-6","instant":"gpt-6-instant",
            "thinking":"gpt-6-thinking","extended":"gpt-6-thinking"})
        self.assertEqual(x["gpt-6-mini"],{
            "instant":"gpt-6-mini","thinking":"gpt-6-t-mini"})
        self.assertEqual([r["name"] for r in bb_acp.model_rows(source_catalog())],
            ["GPT-6","GPT-6 Mini","GPT-5.6 Sol","GPT-5.6 Luna"])
        self.assertEqual(bb_acp.build_families([{"slug":"gpt-6-instant"}]),
                         {"gpt-6":{"instant":"gpt-6-instant"}})

    def test_private_existing_session_migrates_without_changing_chat_id(self):
        with tempfile.TemporaryDirectory() as d, patch.object(bb_acp,"ROOT",Path(d)):
            sid="a"*32
            original={"sessionId":sid,"model":"gpt-6-thinking",
                      "reasoning":"high","conversation_id":"existing-id",
                      "keep":"other-record"}
            bb_acp.save_session(original)
            self.assertEqual(bb_acp.load_session(sid),original)
            self.assertEqual(bb_acp.session_path(sid).stat().st_mode&0o777,0o600)
            self.assertRaises(ValueError,bb_acp.session_path,"../other")
            server=bb_acp.Server()
            with patch.object(bb_acp,"model_capabilities",return_value=source_catalog()):
                server.catalog()
            state=bb_acp.load_session(sid)
            server.normalize_session(state)
            self.assertEqual((state["model"],state["mode"]),
                             ("gpt-6","extended"))
            self.assertEqual(state["conversation_id"],"existing-id")
            self.assertEqual(state["keep"],"other-record")
            self.assertNotIn("reasoning",state)

class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server=bb_acp.Server()
        self.records=[]
        async def emit(record):self.records.append(record)
        self.server.emit=emit
        self.catalog_patch=patch.object(bb_acp,"model_capabilities",return_value=source_catalog())
        self.catalog_patch.start();self.server.catalog()
        self.temp=tempfile.TemporaryDirectory()
        self.root_patch=patch.object(bb_acp,"ROOT",Path(self.temp.name));self.root_patch.start()
    async def asyncTearDown(self):
        self.root_patch.stop();self.catalog_patch.stop();self.temp.cleanup()
    async def request(self,id,method,params):
        await self.server.dispatch({"jsonrpc":"2.0","id":id,"method":method,"params":params})
        return self.records[-1]
    async def test_picker_model_mode_and_concurrent_session_settings(self):
        self.assertEqual((await self.request(1,"initialize",{}))["result"]["protocolVersion"],1)
        r=(await self.request(2,"session/new",{"cwd":"/tmp","mcpServers":[]}))["result"]
        sid=r["sessionId"]
        self.assertEqual(len(r["models"]["availableModels"]),4)
        self.assertEqual(r["models"]["currentModelId"],"gpt-6")
        self.assertEqual([x["id"] for x in r["configOptions"]],["model","mode"])
        self.assertEqual([x["value"] for x in r["configOptions"][1]["options"]],
                         ["auto","instant","thinking","extended"])
        r=(await self.request(3,"session/set_config_option",
             {"sessionId":sid,"configId":"mode","value":"extended"}))["result"]
        self.assertEqual(r["configOptions"][1]["currentValue"],"extended")
        r=(await self.request(4,"session/set_config_option",
             {"sessionId":sid,"configId":"model","value":"gpt-5-6"}))["result"]
        self.assertEqual(r["configOptions"][0]["currentValue"],"gpt-5-6")
        self.assertEqual(r["configOptions"][1]["currentValue"],"extended")
        r=(await self.request(5,"session/set_model",
             {"sessionId":sid,"modelId":"gpt-6-mini"}))["result"]
        self.assertEqual(r["configOptions"][1]["currentValue"],"thinking")
        self.assertEqual([x["value"] for x in r["configOptions"][1]["options"]],
                         ["instant","thinking"])
        self.assertIn("error",await self.request(6,"session/set_config_option",
             {"sessionId":sid,"configId":"mode","value":"extended"}))
        self.assertIn("error",await self.request(7,"session/set_config_option",
             {"sessionId":sid,"configId":"mode","value":"auto"}))
        self.assertIn("error",await self.request(8,"session/set_model",
             {"sessionId":sid,"modelId":"internal"}))
        r=(await self.request(9,"session/load",{"sessionId":sid,"cwd":"/tmp","mcpServers":[]}))["result"]
        self.assertEqual(r["configOptions"][1]["currentValue"],"thinking")
        self.assertEqual(bb_acp.load_session(sid)["model"],"gpt-6-mini")

    async def test_bb_system_envelope_is_not_forwarded_as_user_text(self):
        sid = "c" * 32
        self.server.sessions[sid] = {
            "sessionId": sid, "model": "gpt-6", "mode": "instant",
            "conversation_id": None,
        }
        args_seen = []
        async def fake_exec(*args, **kwargs):
            args_seen.extend(args)
            class Process:
                def __init__(self):
                    self.stdout = asyncio.StreamReader()
                    self.stdout.feed_data(
                        b'{"type":"result","conversation_id":"verified"}\n')
                    self.stdout.feed_eof()
                    self.stderr = asyncio.StreamReader()
                    self.stderr.feed_eof()
                    self.returncode = 0
                async def wait(self): return 0
            return Process()
        payload = ("<system_instructions>" + "bb-only secret tool instructions "*900
                   + "</system_instructions>Send exactly USER-TEXT")
        with patch.object(bb_acp.asyncio, "create_subprocess_exec",
                          side_effect=fake_exec):
            await self.server.do_prompt(54, sid, [{"type": "text", "text": payload}])
        self.assertIn("USER-TEXT", args_seen[-1])
        self.assertNotIn("bb-only secret tool instructions", args_seen[-1])
        self.assertEqual(self.records[-1]["result"]["stopReason"], "end_turn")

    async def test_every_mode_routes_to_verified_native_slug_and_effort(self):
        for family,mode,slug,effort in (
            ("gpt-6","auto","gpt-6",None),
            ("gpt-6","instant","gpt-6-instant",None),
            ("gpt-6","thinking","gpt-6-thinking","standard"),
            ("gpt-6","extended","gpt-6-thinking","extended"),
            ("gpt-6-mini","thinking","gpt-6-t-mini",None),
            ("gpt-6-mini","instant","gpt-6-mini",None),
            ("gpt-5-6","extended","gpt-5-6-thinking","extended"),
            ("gpt-5-6-mini","thinking","gpt-5-6-t-mini",None),
        ):
            with self.subTest(family=family,mode=mode):
                sid="b"*32
                self.server.sessions[sid]={"sessionId":sid,"model":family,
                    "mode":mode,"conversation_id":None}
                calls=[]
                async def fake_exec(*argv,**kwargs):
                    calls.append(list(argv))
                    class FakeProcess:
                        def __init__(self):
                            self.stdout=asyncio.StreamReader()
                            self.stdout.feed_data(b'{"type":"result","conversation_id":"fake-chat"}\n')
                            self.stdout.feed_eof()
                            self.stderr=asyncio.StreamReader()
                            self.stderr.feed_eof()
                            self.returncode=0
                        async def wait(self):return 0
                    return FakeProcess()
                with patch.object(bb_acp.asyncio,"create_subprocess_exec",side_effect=fake_exec):
                    await self.server.do_prompt(30,sid,[{"type":"text","text":"test"}])
                args=calls[-1]
                self.assertEqual(args[args.index("--model")+1],slug)
                if effort:
                    self.assertEqual(args[args.index("--thinking-effort")+1],effort)
                else:
                    self.assertNotIn("--thinking-effort",args)
                self.assertEqual(self.records[-1]["result"]["stopReason"],"end_turn")
