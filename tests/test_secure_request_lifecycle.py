"""The UI must never claim a failed preparation submitted a chat."""
import socket
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from importlib.machinery import SourceFileLoader

from chatgpt_tui import ChatTui


def backend():
    module=types.ModuleType("secure_lifecycle_backend")
    file=Path(__file__).resolve().parents[1]/"chatgpt-web"
    module.__file__=str(file)
    SourceFileLoader("secure_lifecycle_backend",str(file)).exec_module(module)
    return module


class BusyDaemonTests(unittest.TestCase):
    def test_busy_daemon_never_starts_a_second_daemon(self):
        m=backend()
        fake_socket=Mock()
        fake_socket.exists.return_value=True
        with patch.object(m,"daemon_request",side_effect=socket.timeout()),patch.object(
            m,"start_daemon"
        ) as startup:
            with self.assertRaisesRegex(m.ChatGPTWebError,"busy with another request"):
                m.ensure_daemon(fake_socket,"http://127.0.0.1:9222")
            startup.assert_not_called()
        self.assertEqual(fake_socket.exists.call_count,1)

    def test_ready_daemon_has_short_probe(self):
        m=backend()
        socket_path=Mock()
        socket_path.exists.return_value=True
        with patch.object(m,"daemon_request",return_value={"ready":True}) as ping,patch.object(
            m,"start_daemon"
        ) as startup:
            m.ensure_daemon(socket_path,"http://127.0.0.1:9222")
            startup.assert_not_called()
            self.assertEqual(ping.call_args.kwargs["timeout_seconds"],1.5)


class LifecycleTests(unittest.TestCase):
    def test_verification_phases_do_not_include_private_tokens(self):
        m=backend()
        client=object.__new__(m.ChatGPTWebClient)
        client.run_sentinel_node=Mock(side_effect=[
            {"initial":"fake-proof"},
            {"proof":"test-result"},
        ])
        response=Mock()
        response.json.return_value={"prepare_token":"test-token"}
        client.request=Mock(return_value=response)
        phases=[]
        headers=client.create_sentinel_headers(on_phase=phases.append)
        self.assertEqual(len(phases),3)
        self.assertIn("Checking verification requirements",phases[1])
        self.assertNotIn("test-token","".join(phases))
        self.assertEqual(client.request.call_args.kwargs["timeout"],12)
        self.assertIn("openai-sentinel-proof-token",headers)

    def test_network_failure_restores_draft_and_keeps_error_in_transcript(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(
            __import__("os").environ,{"XDG_DATA_HOME":folder}
        ):
            send=Mock(side_effect=TimeoutError("timed out waiting for response"))
            tui=ChatTui(lambda:[],lambda _: {},send)
            tui.input.text="generate an image of Loom"
            tui.submit_prompt()
            for _ in range(300):
                if not tui.busy:
                    break
                time.sleep(0.01)
            self.assertFalse(tui.busy)
            self.assertEqual(send.call_count,1)
            self.assertEqual(tui.input.text,"generate an image of Loom")
            self.assertIn("timed out",tui.messages[-1]["text"])
            self.assertIn("Submission status may be uncertain",tui.messages[-1]["text"])
            self.assertTrue(tui.status.startswith("Request failed"))

    def test_preparation_phases_update_status_then_finish(self):
        holder={}
        def send(cid,prompt,attachments,on_event,on_connection):
            on_event({"type":"phase","status":"Checking CLI Project…"})
            self.assertEqual(holder["t"].status,"Checking CLI Project…")
            on_event({"type":"phase","status":"Waiting for ChatGPT response…","submitted":True})
            self.assertTrue(holder["t"].request_submitted)
            return {"conversation_id":"test-conv","conversation":{"title":"Completed","messages":[]},"media_files":[]}
        with tempfile.TemporaryDirectory() as folder,patch.dict(
            __import__("os").environ,{"XDG_DATA_HOME":folder}
        ):
            tui=ChatTui(lambda:[],lambda _: {},send)
            holder["t"]=tui
            tui.input.text="hello"
            tui.submit_prompt()
            for _ in range(300):
                if not tui.busy:
                    break
                time.sleep(0.01)
            self.assertFalse(tui.busy)
            self.assertEqual(tui.status,"Ready")


if __name__=="__main__":
    unittest.main()
