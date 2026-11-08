"""Verify browser SSE routes public commentary, actual calls and final text."""
import contextlib
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


def load_client():
    path = Path(__file__).resolve().parents[1] / "chatgpt-web"
    return SourceFileLoader("bb_sse_classification_test", str(path)).load_module()


class SSEActivityTests(unittest.TestCase):
    def test_stream_channels_never_mix_commentary_tools_and_answer(self):
        m = load_client()
        client = object.__new__(m.ChatGPTWebClient)
        client.headers = {"authorization": "local-test-only"}
        client.create_sentinel_headers = lambda on_phase=None: {}
        client.create_conversation_body = lambda *_args: {
            "messages": [{"id": "submitted-user-message"}]}
        client.target_project_id = lambda: "verified-project"
        events = []
        sample = [
            {"message": {"id": "internal", "author": {"role": "assistant"},
                         "channel": "analysis", "content": {"parts": ["private analysis"]}}},
            {"message": {"id": "comment", "author": {"role": "assistant"},
                         "channel": "commentary", "content": {"parts": ["Checking"]}}},
            {"message": {"id": "comment", "author": {"role": "assistant"},
                         "channel": "commentary", "content": {"parts": ["Checking files"]}}},
            {"message": {"id": "tool1", "author": {"role": "assistant"},
                         "recipient": "functions.exec", "content": {"parts": ["invoke"]}}},
            {"message": {"id": "tool1", "author": {"role": "assistant"},
                         "recipient": "functions.exec", "content": {"parts": ["invoke"]}}},
            {"message": {"id": "result1", "author": {"role": "tool", "name": "functions.exec"},
                         "status": "finished_successfully",
                         "content": {"parts": ["Tool output"]}}},
            {"message": {"id": "final", "author": {"role": "assistant"},
                         "channel": "final", "content": {"parts": ["Draft"]}}},
            {"conversation_id": "verified-conversation", "message": {
                "id": "final", "author": {"role": "assistant"},
                "channel": "final", "content": {"parts": ["Final answer"]}}},
        ]
        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            def iter_lines(self):
                for row in sample:
                    yield "data: " + json.dumps(row)
                yield "data: [DONE]"
        observed = []
        def fake_stream(*args, **kwargs):
            observed.append(kwargs['timeout'])
            return contextlib.nullcontext(FakeResponse())
        client.http = SimpleNamespace(stream=fake_stream)
        resolved, submitted = client.send_prompt_direct(None, "Test", [], events.append)
        self.assertEqual((resolved, submitted), ("verified-conversation", "submitted-user-message"))
        self.assertEqual([e["type"] for e in events if e.get("type") in
                         ("tool_start", "tool_end", "commentary", "delta", "replace")],
                         ["commentary", "commentary", "tool_start", "tool_end", "delta", "replace"])
        self.assertEqual([e["name"] for e in events if e.get("type") == "tool_start"],
                         ["functions.exec"])
        self.assertEqual([e["text"] for e in events if e.get("type") == "commentary"],
                         ["Checking", "Checking files"])
        self.assertEqual([e["text"] for e in events if e.get("type") == "replace"],
                         ["Final answer"])
        self.assertNotIn("private analysis", str(events))
        self.assertEqual([e for e in events if e.get("type") == "tool_end"],
                         [{"type": "tool_end", "id": "tool1", "status": "completed"}])
        self.assertNotIn("Tool output", str(events))
        self.assertEqual(observed[0].read, 1100.0)
        self.assertEqual(observed[0].connect, 20.0)

if __name__ == "__main__": unittest.main()
