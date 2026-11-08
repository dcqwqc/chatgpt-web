"""Local UI disconnects must not interrupt an accepted source HTTP stream."""
import json
import socket
import threading
import time
import types
import unittest
from pathlib import Path
from importlib.machinery import SourceFileLoader


def backend():
    module = types.ModuleType("daemon_heartbeat_test")
    path = Path(__file__).resolve().parents[1] / "chatgpt-web"
    module.__file__ = str(path)
    SourceFileLoader("daemon_heartbeat_test", str(path)).exec_module(module)
    return module


class StreamHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.m = backend()

    def test_periodic_heartbeat_does_not_fabricate_model_output(self):
        reader, writer = socket.socketpair()
        try:
            with self.m.resilient_stream_sender(writer, interval=0.02) as send:
                reader.settimeout(0.5)
                line = reader.makefile("rb").readline()
                self.assertEqual(json.loads(line), {
                    "ok": True, "event": {"type": "heartbeat"}
                })
                send({"ok": True, "event": {"type": "tool_start", "id": "original"}})
                second = reader.makefile("rb").readline()
                self.assertEqual(json.loads(second)["event"]["id"], "original")
        finally:
            reader.close()
            writer.close()

    def test_closed_client_does_not_abort_provider_worker(self):
        client, server = socket.socketpair()
        try:
            client.close()
            source_completed = []
            with self.m.resilient_stream_sender(server, interval=0.01) as send:
                # Both the streaming update and the final result must tolerate
                # an already-disconnected receiver.
                send({"ok": True, "event": {"type": "phase", "submitted": True}})
                source_completed.append("source continued")
                send({"ok": True, "result": {"conversation_id": "original"}})
            self.assertEqual(source_completed, ["source continued"])
        finally:
            server.close()

    def test_concurrent_heartbeat_and_payload_are_framed_independently(self):
        reader, writer = socket.socketpair()
        rows = []
        try:
            with self.m.resilient_stream_sender(writer, interval=0.01) as send:
                def publish():
                    for i in range(70):
                        send({"ok": True, "event": {"type": "tool_update", "id": str(i)}})
                thread = threading.Thread(target=publish)
                thread.start()
                thread.join(1)
                self.assertFalse(thread.is_alive())
                reader.settimeout(0.5)
                reader_file = reader.makefile("rb")
                for i in range(70):
                    rows.append(json.loads(reader_file.readline()))
            payloads = [r["event"]["id"] for r in rows if r["event"]["type"] == "tool_update"]
            self.assertEqual(payloads, [str(i) for i in range(70)])
        finally:
            reader.close()
            writer.close()


if __name__ == "__main__":
    unittest.main()
