"""Transient read failures must not turn into duplicate chat submissions."""
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from importlib.machinery import SourceFileLoader
import httpx


def backend():
    module=types.ModuleType("read_retry_backend")
    f=Path(__file__).resolve().parents[1]/"chatgpt-web"
    module.__file__=str(f)
    SourceFileLoader("read_retry_backend",str(f)).exec_module(module)
    return module


class RequestRetryTests(unittest.TestCase):
    def setUp(self):
        self.m=backend()
        self.client=object.__new__(self.m.ChatGPTWebClient)
        self.client.headers={"authorization":"test"}
        self.client.http=Mock()
        self.url="https://chatgpt.com/backend-api/models"

    def test_get_recovers_after_transient_timeout(self):
        response=Mock(status_code=200,headers={"content-type":"application/json"})
        self.client.http.request.side_effect=[httpx.ReadTimeout("timeout"),response]
        with patch.object(self.m.time,"sleep") as sleep:
            self.assertIs(self.client.request("GET",self.url),response)
        self.assertEqual(self.client.http.request.call_count,2)
        sleep.assert_called_once_with(0.5)

    def test_post_timeout_does_not_retry(self):
        self.client.http.request.side_effect=httpx.ReadTimeout("timed out")
        with self.assertRaises(httpx.ReadTimeout):
            self.client.request("POST",self.url,json={"test":True})
        self.assertEqual(self.client.http.request.call_count,1)

    def test_get_failure_names_safe_endpoint_and_stops(self):
        self.client.http.request.side_effect=httpx.ReadTimeout("timed out")
        with patch.object(self.m.time,"sleep"):
            with self.assertRaisesRegex(self.m.ChatGPTWebError,
                r"GET /backend-api/models timed out after 3 read attempts"):
                self.client.request("GET",self.url)
        self.assertEqual(self.client.http.request.call_count,3)

    def test_429_is_not_retried(self):
        response=Mock(status_code=429,headers={
            "retry-after":"60","content-type":"application/json"})
        self.client.http.request.return_value=response
        with self.assertRaisesRegex(self.m.ChatGPTWebError,"retry after 60"):
            self.client.request("GET",self.url)
        self.assertEqual(self.client.http.request.call_count,1)


class ReadOnlyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.m = backend()
        self.client = object.__new__(self.m.ChatGPTWebClient)
        self.client.target_project_id = Mock(return_value="proj")
        self.client.get_conversation = Mock()
        self.client.send_prompt_direct = Mock()
        self.before = {"gizmo_id": "proj", "messages": [{"id": "old", "author": {"role": "user"}}]}

    @staticmethod
    def saved_answer(*, done=True, channel="final", msg="Submitted answer"):
        return {"gizmo_id": "proj", "messages": [
            {"id": "old", "author": {"role": "user"}},
            {"id": "submitted-uuid", "author": {"role": "user"},
             "status": "finished_successfully"},
            {"id": "final-uuid", "author": {"role": "assistant"},
             "recipient": "all", "channel": channel,
             "status": "finished_successfully" if done else "in_progress",
             "content": {"parts": [msg]}}
        ]}

    def test_exact_saved_final_recovers_without_resend(self):
        self.client.get_conversation.side_effect = [self.before, self.saved_answer()]
        def timeout_after_accept(_previous, _prompt, _attachments, callback):
            callback({"type":"submission_identity", "message_id":"submitted-uuid"})
            callback({"type":"stream_conversation_id", "conversation_id":"conversation"})
            callback({"type":"phase", "status":"Waiting", "submitted":True})
            raise httpx.ReadTimeout("silent stream")
        self.client.send_prompt_direct.side_effect = timeout_after_accept
        events = []
        cid, saved, old = self.client.send_prompt(
            "conversation", "user question", on_event=events.append)
        self.assertEqual(cid, "conversation")
        self.assertEqual(saved["messages"][-1]["id"], "final-uuid")
        self.assertEqual(old, {"old"})
        self.client.send_prompt_direct.assert_called_once()
        self.assertTrue(any("Recovered verified" in e.get("status", "") for e in events))

    def test_unfinished_reply_does_not_recover_or_resend(self):
        for saved in (self.saved_answer(done=False),
                      self.saved_answer(channel="commentary"),
                      self.saved_answer(msg="")):
            with self.subTest(saved=saved):
                self.client.get_conversation.side_effect = [self.before, saved]
                def timeout(_previous, _prompt, _attachments, callback):
                    callback({"type":"submission_identity", "message_id":"submitted-uuid"})
                    raise httpx.ReadTimeout("stream timed out")
                self.client.send_prompt_direct.side_effect = timeout
                with self.assertRaises(httpx.ReadTimeout):
                    self.client.send_prompt("conversation", "test")
                self.client.send_prompt_direct.assert_called_once()
                self.client.send_prompt_direct.reset_mock()
                self.client.get_conversation.reset_mock()

    def test_unrelated_message_id_never_recovers(self):
        self.client.get_conversation.side_effect = [self.before, self.saved_answer()]
        def timeout(_previous, _prompt, _attachments, callback):
            callback({"type":"submission_identity", "message_id":"another-id"})
            raise httpx.ReadTimeout("stream timed out")
        self.client.send_prompt_direct.side_effect = timeout
        with self.assertRaises(httpx.ReadTimeout):
            self.client.send_prompt("conversation", "test")
        self.client.send_prompt_direct.assert_called_once()
