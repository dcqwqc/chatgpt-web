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

class BbCorePolicyTests(unittest.TestCase):
    def test_published_policy_is_read_from_bb_data_dir(self):
        value = bb_acp.load_bb_core_policy()
        self.assertIn("Shared Agent Operating Contract", value)
        self.assertGreater(len(value), 1000)

    def test_missing_or_empty_core_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            bb_acp.Path, "home", return_value=Path(d)
        ):
            with self.assertRaisesRegex(RuntimeError, "not published"):
                bb_acp.load_bb_core_policy()
            folder = Path(d) / ".bb"
            folder.mkdir()
            (folder / "AGENTS.md").write_text("")
            with self.assertRaisesRegex(RuntimeError, "empty"):
                bb_acp.load_bb_core_policy()


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
        self.assertIn("[BB Agent Control — published Core Instructions]", args_seen[-1])
        self.assertIn("Shared Agent Operating Contract", args_seen[-1])
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

class GeneratedMediaTests(unittest.IsolatedAsyncioTestCase):
    def test_verified_cached_images_only(self):
        import json
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            media = root / "images"
            conversations = root / "conversations"
            media.mkdir(); conversations.mkdir()
            name = "file_00000000fb3c81f4aa4a0d784f93834b"
            (media / (name+".png")).write_bytes(b"\x89PNG\r\n\x1a\nrest")
            cid = "6ae3116a-1fa8-83ed-aa4a-12c775163884"
            (conversations / (cid+".json")).write_text(json.dumps({"media_files":[
                str(media / (name+".png")), "/etc/passwd", str(media / "../wrong.png")
            ]}))
            with patch.object(bb_acp, "MEDIA_ROOT", media), patch.object(bb_acp, "CONVERSATION_ROOT", conversations):
                self.assertEqual(bb_acp.generated_media(cid), [name])
                self.assertEqual(bb_acp.generated_media("../etc/passwd"), [])
                self.assertIn(name, bb_acp.markdown_images([name]))
    
    async def test_image_only_reply_emits_markdown_in_bb(self):
        import json
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); media=root/"images"; convs=root/"conversations"
            media.mkdir(); convs.mkdir()
            cid = "6ae3116a-1fa8-83ed-aa4a-12c775163884"
            image_id = "file_00000000fb3c81f4aa4a0d784f93834b"
            image = media / (image_id + ".png")
            events=[]
            server=bb_acp.Server()
            async def emit(obj): events.append(obj)
            server.emit=emit
            sid="a"*32
            server.sessions[sid]={"sessionId":sid,"conversation_id":None,
                                  "model":"gpt-6","mode":"instant"}
            with patch.object(bb_acp, "ROOT", root/"sessions"), patch.object(bb_acp,"MEDIA_ROOT",media),patch.object(bb_acp,"CONVERSATION_ROOT",convs),patch.object(bb_acp,"model_capabilities",return_value=source_catalog()):
                server.catalog()
                async def fake_exec(*args,**kwargs):
                    image.write_bytes(b"\x89PNG\r\n\x1a\nrest")
                    (convs/(cid+".json")).write_text(json.dumps({"media_files":[str(image)]}))
                    class Process:
                        def __init__(self):
                            self.stdout=asyncio.StreamReader()
                            self.stdout.feed_data(json.dumps({"type":"result","conversation_id":cid}).encode()+b"\n")
                            self.stdout.feed_eof()
                            self.stderr=asyncio.StreamReader();self.stderr.feed_eof()
                            self.returncode=0
                        async def wait(self):return 0
                    return Process()
                with patch.object(bb_acp.asyncio,"create_subprocess_exec",side_effect=fake_exec):
                    await server.do_prompt(123,sid,[{"type":"text","text":"generate a tree image"}])
                chunks=[obj["params"]["update"]["content"]["text"] for obj in events if obj.get("method")=="session/update"]
                self.assertEqual(len(chunks),1)
                self.assertIn(image_id,chunks[0])
                self.assertNotIn("no text",chunks[0])
                self.assertEqual(events[-1]["result"]["stopReason"],"end_turn")

class ImageFallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_only_explicit_image_intents_trigger_fallback(self):
        self.assertTrue(bb_acp.explicit_image_intent("Generate an image of a tree."))
        self.assertTrue(bb_acp.explicit_image_intent("Please draw a picture of a tree."))
        self.assertFalse(bb_acp.explicit_image_intent("How does image generation work?"))
        self.assertFalse(bb_acp.explicit_image_intent("What is a tree?"))
        self.assertTrue(bb_acp.image_edit_intent("Make it orange"))
        self.assertFalse(bb_acp.image_edit_intent("Explain the image generation algorithm"))

    async def test_fallback_on_preparation_error_returns_real_worker_image(self):
        from unittest.mock import AsyncMock
        import json
        with tempfile.TemporaryDirectory() as work:
            root=Path(work)
            saved=root/("d1c34b5d60824e228fe6ec7e61cfea5d")
            saved.write_bytes(b"\x89PNG\r\n\x1a\nrest")
            sid="b"*32
            server=bb_acp.Server()
            server.sessions[sid]={"sessionId":sid,"conversation_id":None,"model":"gpt-6","mode":"instant"}
            records=[]
            async def emit(obj):records.append(obj)
            server.emit=emit
            class FakeProcess:
                def __init__(self):
                    self.stdout=asyncio.StreamReader()
                    self.stdout.feed_eof()
                    self.stderr=asyncio.StreamReader()
                    self.stderr.feed_data(b"chatgpt-web: Chrome request preparation failed")
                    self.stderr.feed_eof()
                    self.returncode=1
                async def wait(self):return 1
            async def fake_exec(*args,**kwargs):return FakeProcess()
            calls=[]
            async def fake_bridge(action,**data):
                calls.append((action,data))
                if action=="generate":
                    return {"job_id":"f"*32,"state":"queued"}
                return {"state":"completed","image":{
                    "path":str(saved),"session_id":"e"*32,
                    "version_id":"c"*32}}
            with patch.object(bb_acp,"ROOT",root/"sessions"),patch.object(
                bb_acp,"model_capabilities",return_value=source_catalog()),patch.object(
                bb_acp.asyncio,"create_subprocess_exec",side_effect=fake_exec),patch.object(
                bb_acp,"image_bridge_call",side_effect=fake_bridge),patch.object(
                bb_acp.asyncio,"sleep",new_callable=AsyncMock):
                server.catalog()
                await server.do_prompt(12,sid,[{"type":"text","text":"Generate an image of a tree."}])
            updates=[r["params"]["update"]["content"]["text"] for r in records if r.get("method")=="session/update"]
            self.assertIn("/worker-image?asset=d1c34b5d60824e228fe6ec7e61cfea5d","".join(updates))
            self.assertEqual(calls[0][0],"generate")
            self.assertEqual(calls[0][1]["prompt"],"Generate an image of a tree.")
            self.assertEqual(records[-1]["result"]["stopReason"],"end_turn")
            self.assertEqual(json.loads((root / "sessions" / (sid+".json")).read_text())["image_version_id"],"c"*32)


class RateLimitRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = bb_acp.Server()
        self.records = []
        async def emit(record):
            self.records.append(record)
        self.server.emit = emit
        self.catalog_patch = patch.object(
            bb_acp, "model_capabilities", return_value=source_catalog())
        self.catalog_patch.start()
        self.server.catalog()
        self.temp = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(bb_acp, "ROOT", Path(self.temp.name))
        self.root_patch.start()
        self.sid = "d" * 32
        self.server.sessions[self.sid] = {
            "sessionId": self.sid, "model": "gpt-6",
            "mode": "instant", "conversation_id": None,
        }

    async def asyncTearDown(self):
        self.root_patch.stop()
        self.catalog_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def process(*, failed=False, submitted=False, delay=58):
        class FakeProcess:
            def __init__(self):
                self.stdout = asyncio.StreamReader()
                if submitted:
                    self.stdout.feed_data(
                        b'{"type":"phase","status":"Verifying saved conversation","submitted":true}\n')
                if not failed:
                    self.stdout.feed_data(
                        b'{"type":"result","conversation_id":"verified"}\n')
                self.stdout.feed_eof()
                self.stderr = asyncio.StreamReader()
                if failed:
                    self.stderr.feed_data(
                        f"chatgpt-web: ChatGPT web rate limit reached; "
                        f"retry after {delay} seconds".encode())
                self.stderr.feed_eof()
                self.returncode = 1 if failed else 0

            async def wait(self):
                return self.returncode

        return FakeProcess()

    async def test_unsubmitted_429_retries_and_preserves_original_request(self):
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return self.process(failed=len(calls) == 1)
        async def instant_sleep(seconds):
            self.assertEqual(seconds, 59)
        with patch.object(bb_acp.asyncio, "create_subprocess_exec",
                          side_effect=fake_exec), patch.object(
                              bb_acp.asyncio, "sleep",
                              side_effect=instant_sleep):
            await self.server.do_prompt(
                9, self.sid, [{"type": "text", "text": "recommend plugins for BB"}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.server.sessions[self.sid]["conversation_id"],
                         "verified")
        self.assertEqual(self.records[-1]["result"]["stopReason"], "end_turn")
        self.assertEqual(len([r for r in self.records if "result" in r]), 1)

    async def test_post_submit_429_does_not_resend(self):
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return self.process(failed=True, submitted=True)
        with patch.object(bb_acp.asyncio, "create_subprocess_exec",
                          side_effect=fake_exec):
            with self.assertRaisesRegex(RuntimeError, "rate limit reached"):
                await self.server.do_prompt(
                    10, self.sid, [{"type":"text","text":"do not duplicate me"}])
        self.assertEqual(len(calls), 1)

    async def test_stops_after_two_retry_attempts(self):
        calls = []
        sleeps = []
        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return self.process(failed=True)
        async def fake_sleep(seconds):
            sleeps.append(seconds)
        with patch.object(bb_acp.asyncio, "create_subprocess_exec",
                          side_effect=fake_exec), patch.object(
                              bb_acp.asyncio, "sleep",
                              side_effect=fake_sleep):
            with self.assertRaisesRegex(RuntimeError, "retry after 58 seconds"):
                await self.server.do_prompt(
                    11, self.sid, [{"type":"text","text":"retry with bounds"}])
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [59, 59])

    async def test_unreasonable_retry_after_is_not_retried(self):
        calls = []
        async def fake_exec(*args, **kwargs):
            calls.append(args)
            return self.process(failed=True, delay=3600)
        with patch.object(bb_acp.asyncio, "create_subprocess_exec",
                          side_effect=fake_exec):
            with self.assertRaisesRegex(RuntimeError, "retry after 3600 seconds"):
                await self.server.do_prompt(
                    12, self.sid, [{"type":"text","text":"bounded wait"}])
        self.assertEqual(len(calls), 1)
