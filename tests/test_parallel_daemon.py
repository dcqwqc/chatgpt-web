"""True simultaneous CLI terminal connections, not serial fake send calls."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import types
from importlib.machinery import SourceFileLoader
from unittest import TestCase
from unittest.mock import patch
import unittest


def backend():
    module = types.ModuleType("parallel_backend_test")
    source = Path(__file__).resolve().parents[1] / "chatgpt-web"
    module.__file__ = str(source)
    SourceFileLoader("parallel_backend_test", str(source)).exec_module(module)
    return module


class FakeClient:
    def __init__(self, control):
        self.control = control
        self.cdp_url = "http://127.0.0.1:9222"
        self.list_url = "https://chatgpt.com/backend-api/conversations"
        self.headers = {"authorization": "test-only"}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def send_prompt(self, cid, prompt, attachments, on_event=None, **kwargs):
        self.control["entered"].append((cid,prompt,threading.get_ident()))
        self.control["started"].set()
        if cid == "long-one":
            self.control["first_started"].set()
        if cid == "long-two":
            self.control["second_started"].set()
        if cid and cid.startswith("long-"):
            if on_event:
                on_event({"type":"phase","status":"Waiting for ChatGPT response…","submitted":True})
            if not self.control["release"].wait(5):
                raise RuntimeError("Fake send synchronization timed out")
        rid = cid or "new-" + prompt.replace(" ", "-")
        return rid, {"conversation_id":rid,"id":rid,"gizmo_id":"g-p-"+("a"*32),
                     "title":"test","messages":[]}, set()


@contextmanager
def background_daemon():
    module = backend()
    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
        "XDG_DATA_HOME": root,
        "XDG_RUNTIME_DIR": root,
    }):
        control = {
            "started":threading.Event(),
            "first_started":threading.Event(),
            "second_started":threading.Event(),
            "release":threading.Event(),
            "entered":[],
        }
        path = Path(root) / "web.sock"
        errors = []
        def launch():
            try:
                module.run_daemon(path, "http://127.0.0.1:9222")
            except Exception as error:
                errors.append(error)
        def fakeclient(*args):
            return FakeClient(control)
        with patch.object(module,"ChatGPTWebClient",side_effect=fakeclient),patch.object(
            module,"client_for_connection",side_effect=lambda *_:FakeClient(control)
        ),patch.object(module.signal,"signal"):
            daemon = threading.Thread(target=launch,daemon=True)
            daemon.start()
            for _ in range(100):
                if path.exists(): break
                time.sleep(0.02)
            if not path.exists():
                raise RuntimeError("daemon socket did not start: "+repr(errors))
            try:
                yield module, path, control
            finally:
                control["release"].set()
                try:
                    module.daemon_request(path, {"command":"stop"},timeout_seconds=3)
                except (OSError,RuntimeError):
                    pass
                daemon.join(timeout=5)
                if daemon.is_alive():
                    errors.append(RuntimeError("Daemon did not stop"))
                if errors:
                    raise errors[0]


class ParallelDaemonIntegrationTests(TestCase):
    def test_bootstrap_uses_saved_auth_without_waiting_for_browser_capture(self):
        m = backend()
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
            "XDG_DATA_HOME": root, "XDG_RUNTIME_DIR": root,
        }):
            path = Path(root) / "auth-bootstrap.sock"
            control = {"entered": [], "started": threading.Event(),
                       "first_started": threading.Event(),
                       "second_started": threading.Event(),
                       "release": threading.Event()}
            errors = []
            with patch.object(m, "load_auth_snapshot", return_value=(
                "https://chatgpt.com/backend-api/conversations",
                {"authorization": "test-only"},
            )), patch.object(m, "ChatGPTWebClient", side_effect=AssertionError(
                "browser capture should not run when saved auth exists"
            )), patch.object(m, "client_for_connection",
                side_effect=lambda *_: FakeClient(control)
            ), patch.object(m.signal, "signal"):
                def launch():
                    try:
                        m.run_daemon(path, "http://127.0.0.1:9334")
                    except Exception as error:
                        errors.append(error)
                daemon = threading.Thread(target=launch, daemon=True)
                daemon.start()
                for _ in range(100):
                    if path.exists():
                        break
                    time.sleep(0.02)
                self.assertTrue(path.exists(), repr(errors))
                self.assertTrue(m.daemon_request(path, {"command": "ping"})["ready"])
                m.daemon_request(path, {"command": "stop"})
                daemon.join(5)
                self.assertFalse(daemon.is_alive())
                self.assertFalse(errors, repr(errors))

    def test_two_terminals_stream_simultaneously_without_cross_talk(self):
        with background_daemon() as (m,path,control):
            outcomes = {}
            def user(name, cid):
                try:
                    phases=[]
                    response=m.daemon_stream_request(
                        path,{"command":"send_stream","conversation_id":cid,
                              "prompt":name,"attachments":[]},
                        on_event=phases.append,
                    )
                    outcomes[name] = (response, phases)
                except Exception as error:
                    outcomes[name]=error
            one=threading.Thread(target=user,args=("terminal 1","long-one"))
            two=threading.Thread(target=user,args=("terminal 2","long-two"))
            one.start()
            self.assertTrue(control["first_started"].wait(2))
            two.start()
            self.assertTrue(control["second_started"].wait(2),
                            "Second terminal must begin before the first finishes")
            start=time.perf_counter()
            reply=m.daemon_request(path,{"command":"ping"},timeout_seconds=1.5)
            self.assertTrue(reply["ready"])
            self.assertLess(time.perf_counter()-start,1.0)
            self.assertNotIn("terminal 1",outcomes)
            self.assertNotIn("terminal 2",outcomes)
            control["release"].set()
            one.join(3)
            two.join(3)
            self.assertFalse(one.is_alive())
            self.assertFalse(two.is_alive())
            self.assertNotIsInstance(outcomes["terminal 1"],Exception,repr(outcomes["terminal 1"]))
            self.assertNotIsInstance(outcomes["terminal 2"],Exception,repr(outcomes["terminal 2"]))
            self.assertEqual(outcomes["terminal 1"][0]["conversation_id"],"long-one")
            self.assertEqual(outcomes["terminal 2"][0]["conversation_id"],"long-two")
            self.assertEqual(outcomes["terminal 1"][1][0]["type"],"phase")
            self.assertEqual(outcomes["terminal 2"][1][0]["type"],"phase")
            self.assertNotEqual(control["entered"][0][2],control["entered"][1][2])
            self.assertTrue((Path(os.environ["XDG_DATA_HOME"])/"chatgpt-web"/"conversations"/"long-one.json").exists())
            self.assertTrue((Path(os.environ["XDG_DATA_HOME"])/"chatgpt-web"/"conversations"/"long-two.json").exists())

    def test_same_conversation_conflict_is_explicit_and_non_destructive(self):
        with background_daemon() as (m,path,control):
            outcome=[]
            first=threading.Thread(target=lambda: outcome.append(
                m.daemon_stream_request(path,{"command":"send_stream",
                    "conversation_id":"long-one","prompt":"first","attachments":[]},
                    on_event=lambda *_:None)
            ))
            first.start()
            self.assertTrue(control["first_started"].wait(2))
            with self.assertRaisesRegex(m.ChatGPTWebError,"already sending in this conversation"):
                m.daemon_stream_request(path,{"command":"send_stream",
                    "conversation_id":"long-one","prompt":"second","attachments":[]},
                    on_event=lambda *_:None)
            self.assertEqual(len(control["entered"]),1)
            control["release"].set()
            first.join(3)
            self.assertEqual(len(outcome),1)

    def test_concurrent_json_cache_writes_use_unique_temporary_files(self):
        with tempfile.TemporaryDirectory() as root:
            m=backend()
            file=Path(root)/"cached.json"
            barrier=threading.Barrier(6)
            errors=[]
            def write(index):
                try:
                    barrier.wait(3)
                    for _ in range(3):
                        m.atomic_json_write(file, {"index":index, "payload":"x"*1000})
                except Exception as error:
                    errors.append(error)
            threads=[threading.Thread(target=write,args=(i,)) for i in range(6)]
            for t in threads:t.start()
            for t in threads:t.join(6)
            self.assertFalse(errors)
            self.assertFalse(any(t.is_alive() for t in threads))
            obj=json.loads(file.read_text())
            self.assertIn(obj["index"],range(6))
            self.assertEqual(len(obj["payload"]),1000)
            self.assertFalse(list(Path(root).glob("*.tmp")))


if __name__=="__main__":
    unittest.main()

class TerminalSessionIsolationTests(TestCase):
    def test_new_terminal_starts_fresh_instead_of_reusing_global_active(self):
        m=backend()
        with patch.object(m.sys,"argv",["chatgpt-web"]),patch.object(
            m.sys.stdin,"isatty",return_value=True,
        ),patch.object(m,"load_cached_conversation") as previous,patch.object(
            m,"run_fullscreen",return_value=0
        ) as run:
            self.assertEqual(m.main(),0)
            previous.assert_not_called()
            self.assertIsNone(run.call_args.kwargs.get("cached_conversation"))
            self.assertNotIn("cached_conversation",run.call_args.kwargs)

    def test_explicit_resume_keeps_requested_conversation(self):
        m=backend()
        with patch.object(m.sys,"argv",["chatgpt-web","resume","conversation-abc"]),patch.object(
            m,"load_cached_conversation",return_value={"id":"conversation-abc"}
        ) as cached,patch.object(m,"run_fullscreen",return_value=0) as run:
            self.assertEqual(m.main(),0)
            cached.assert_called_once_with("conversation-abc")
            self.assertEqual(run.call_args.args[1],"conversation-abc")
