import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from importlib.machinery import SourceFileLoader
from unittest.mock import patch, Mock
from cli_preferences import parse_projects, parse_models, load_preferences, save_preferences, web_usage_status


PROJECT_ID = "g-p-" + "a" * 32


def load_application():
    m = types.ModuleType("app_under_test")
    source = Path(__file__).resolve().parents[1] / "chatgpt-web"
    SourceFileLoader("app_under_test", str(source)).exec_module(m)
    return m


class PreferencesTests(unittest.TestCase):
    def test_project_name_is_exact_and_nested(self):
        response = {"items": [
            {"gizmo": {"gizmo": {"id": PROJECT_ID, "display": {"name": "CLI"}}}},
            {"gizmo": {"gizmo": {"id": "g-p-" + "b"*32, "display": {"name": "CLI-archived"}}}},
        ]}
        self.assertEqual(parse_projects(response), [{"id": PROJECT_ID, "name": "CLI"}])

    def test_model_catalog_filters_non_infer_and_work_models(self):
        data = {"models": [
            {"slug": "gpt-6", "title": "GPT-6", "enable_infer": True},
            {"slug": "research", "title": "Deep Research", "enable_infer": False},
            {"slug": "work-mode", "title": "Work", "is_work_mode_model": True},
        ]}
        self.assertEqual(parse_models(data), [{"slug": "gpt-6", "title": "GPT-6"}])

    def test_settings_are_private_and_persistent(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            save_preferences(project_id=PROJECT_ID, model="gpt-6-thinking")
            self.assertEqual(load_preferences()["model"], "gpt-6-thinking")
            self.assertEqual(load_preferences()["project_id"], PROJECT_ID)
            self.assertEqual(Path(d, "chatgpt-web/settings.json").stat().st_mode & 0o777, 0o600)

    def test_web_usage_does_not_fabricate_a_quota(self):
        status = web_usage_status()
        self.assertIsNone(status["remaining_messages"])
        self.assertIn("different allowance", status["note"])


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.app = load_application()
        self.client = object.__new__(self.app.ChatGPTWebClient)

    def test_send_body_has_project_and_selected_model(self):
        with patch.object(self.client, "target_project_id", return_value=PROJECT_ID), patch.object(
            self.app, "load_preferences", return_value={"model": "gpt-6-thinking"}
        ):
            body = self.client.create_conversation_body(None, "hello")
        self.assertEqual(body["gizmo_id"], PROJECT_ID)
        self.assertEqual(body["conversation_template_id"], PROJECT_ID)
        self.assertEqual(body["conversation_mode"], {"kind": "gizmo_interaction", "gizmo_id": PROJECT_ID})
        self.assertEqual(body["model"], "gpt-6-thinking")

    def test_nonproject_continuation_is_blocked_before_submit(self):
        self.client.target_project_id = Mock(return_value=PROJECT_ID)
        self.client.get_conversation = Mock(return_value={"gizmo_id": None})
        self.client.send_prompt_direct = Mock()
        with self.assertRaisesRegex(self.app.ChatGPTWebError, "outside the CLI Project"):
            self.client.send_prompt("other-chat-id", "hello")
        self.client.send_prompt_direct.assert_not_called()

    def test_new_chat_must_confirm_project(self):
        self.client.target_project_id = Mock(return_value=PROJECT_ID)
        self.client.send_prompt_direct = Mock(return_value=("test-chat", "user-msg"))
        self.client.get_conversation = Mock(return_value={"gizmo_id": None})
        self.client.request = Mock()
        with self.assertRaisesRegex(self.app.ChatGPTWebError, "did not preserve the requested conversation/Project"):
            self.client.send_prompt(None, "hello")
        self.client.request.assert_called_once()

    def test_new_chat_with_confirmed_project_succeeds(self):
        self.client.target_project_id = Mock(return_value=PROJECT_ID)
        self.client.send_prompt_direct = Mock(return_value=("test-chat", "user-msg"))
        self.client.get_conversation = Mock(return_value={
            "gizmo_id": PROJECT_ID, "messages": [{"id": "user-msg"}]
        })
        result = self.client.send_prompt(None, "hello")
        self.assertEqual(result[0], "test-chat")

    def test_project_list_isolated(self):
        self.client.target_project_id = Mock(return_value=PROJECT_ID)
        self.client.request = Mock(return_value=Mock(json=lambda: {
            "items": [{"id": "one", "gizmo_id": PROJECT_ID}, {"id": "other", "gizmo_id": None}],
            "cursor": None
        }))
        rows = self.client.list_project_conversations()
        self.assertEqual([r["id"] for r in rows], ["one"])

    def test_chrome_mode_fails_closed(self):
        with patch.dict(os.environ, {"CHATGPT_WEB_SEND_MODE": "chrome"}):
            with self.assertRaisesRegex(self.app.ChatGPTWebError, "cannot guarantee"):
                self.client.send_prompt(None, "hello")


if __name__ == "__main__":
    unittest.main()


class InteractiveContinuationTests(unittest.TestCase):
    def setUp(self):
        self.app = load_application()
        self.client = object.__new__(self.app.ChatGPTWebClient)
        self.client.target_project_id = Mock(return_value=PROJECT_ID)

    def test_interactive_normal_chat_preserves_original_context(self):
        before = {"id":"external-chat","gizmo_id":None,"messages":[]}
        after = {"id":"external-chat","gizmo_id":None,
                 "messages":[{"id":"submitted-user"}]}
        self.client.get_conversation = Mock(side_effect=[before, after])
        self.client.send_prompt_direct = Mock(return_value=("external-chat","submitted-user"))
        cid, _, _ = self.client.send_prompt(
            "external-chat", "hello", allow_existing_outside_project=True
        )
        self.assertEqual(cid, "external-chat")
        self.client.send_prompt_direct.assert_called_once()

    def test_cross_project_route_must_not_change(self):
        before = {"id":"external-chat","gizmo_id":None,"messages":[]}
        after = {"id":"external-chat","gizmo_id":PROJECT_ID,
                 "messages":[{"id":"submitted-user"}]}
        self.client.get_conversation = Mock(side_effect=[before,after])
        self.client.send_prompt_direct = Mock(return_value=("external-chat","submitted-user"))
        with self.assertRaisesRegex(self.app.ChatGPTWebError,"did not preserve"):
            self.client.send_prompt("external-chat","hello",allow_existing_outside_project=True)

    def test_body_for_normal_chat_excludes_cli_project(self):
        body = self.client.create_conversation_body(
            {"id":"normal-chat","gizmo_id":None,"current_node":"parent"}, "hello")
        self.assertNotIn("gizmo_id", body)
        self.assertNotIn("conversation_template_id", body)
        self.assertEqual(body["conversation_mode"], {"kind":"primary_assistant"})
        self.assertEqual(body["conversation_id"], "normal-chat")
        self.assertEqual(body["parent_message_id"], "parent")

    def test_body_for_other_project_keeps_original_project(self):
        other = "g-p-" + "b"*32
        body = self.client.create_conversation_body({"id":"other-chat","gizmo_id":other}, "hi")
        self.assertEqual(body["gizmo_id"], other)
        self.assertEqual(body["conversation_template_id"], other)
        self.assertNotEqual(body["gizmo_id"], PROJECT_ID)
