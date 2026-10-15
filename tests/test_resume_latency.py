"""Cached-first conversation picker and bounded Project discovery regressions."""
import threading
import time
import unittest
from unittest.mock import Mock, patch
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path

from chatgpt_tui import ChatTui


def backend_module():
    module = types.ModuleType("resume_backend_test")
    source = Path(__file__).resolve().parents[1] / "chatgpt-web"
    module.__file__ = str(source)
    SourceFileLoader("resume_backend_test", str(source)).exec_module(module)
    return module


class CacheFirstPickerTests(unittest.TestCase):
    def test_cached_results_display_while_remote_is_stalled(self):
        refresh_started = threading.Event()
        release = threading.Event()
        def slow_refresh():
            refresh_started.set()
            release.wait(2)
            return [{"id":"remote-0001","title":"Remote chat"}]
        tui = ChatTui(
            lambda: [{"id":"local:local-" + "f"*32,"title":"Previously saved image"}],
            lambda _: {},lambda *args:{},refresh_conversations=slow_refresh,
        )
        began = time.perf_counter()
        try:
            tui.open_resume()
            self.assertLess(time.perf_counter()-began, 0.5)
            self.assertEqual(len(tui.resume_items),1)
            self.assertEqual(tui.resume_items[0]["title"],"Previously saved image")
            self.assertFalse(tui.resume_loading)
            self.assertTrue(refresh_started.wait(1))
            self.assertTrue(tui.resume_refreshing)
            tui.resume_search.text="saved"
            tui.filter_resume()
            self.assertEqual(len(tui.resume_filtered),1)
        finally:
            release.set()
        for _ in range(40):
            if not tui.resume_refreshing:
                break
            time.sleep(0.01)
        self.assertFalse(tui.resume_refreshing)
        self.assertEqual(len(tui.resume_filtered),0)

    def test_stale_refresh_never_overwrites_new_picker(self):
        blocked=threading.Event()
        release=threading.Event()
        def slow_refresh():
            blocked.set()
            release.wait(2)
            return [{"id":"stale","title":"Do not show"}]
        tui=ChatTui(lambda: [{"id":"cached","title":"Already saved"}],
                     lambda _: {},lambda *args:{},
                     refresh_conversations=slow_refresh)
        tui.open_resume()
        self.assertTrue(blocked.wait(1))
        tui.close_resume()
        # Reopen with refresh disabled; previous refresh must not update list.
        tui.refresh_conversations=None
        tui.open_resume()
        release.set()
        time.sleep(0.05)
        self.assertEqual([x["id"] for x in tui.resume_items],["cached"])
        self.assertEqual(tui.resume_items[0]["title"],"Already saved")

    def test_failure_keeps_saved_list(self):
        def failed():
            raise TimeoutError("ChatGPT is slow")
        tui=ChatTui(lambda:[{"id":"saved","title":"Local chat"}],
                    lambda _: {},lambda *args:{},
                    refresh_conversations=failed)
        tui.open_resume()
        for _ in range(60):
            if not tui.resume_refreshing:
                break
            time.sleep(0.01)
        self.assertEqual(len(tui.resume_filtered),1)
        self.assertIn("refresh unavailable",tui.status)
        self.assertIsNone(tui.resume_error)

    def test_empty_cache_shows_loading_then_remote(self):
        tui=ChatTui(lambda:[],lambda _: {},lambda *args:{},
                    refresh_conversations=lambda:[{"id":"remote","title":"Cloud chat"}])
        tui.open_resume()
        for _ in range(60):
            if not tui.resume_refreshing:
                break
            time.sleep(0.01)
        self.assertEqual([x["id"] for x in tui.resume_items],["remote"])


class ProjectListingTests(unittest.TestCase):
    def test_quick_page_requires_only_one_bounded_request(self):
        m=backend_module()
        cli=object.__new__(m.ChatGPTWebClient)
        project="g-p-"+"a"*32
        response={
            "items":[{"id":"chat-001","gizmo_id":project}],
            "cursor":"another-page",
        }
        cli.target_project_id=Mock(return_value=project)
        cli.request=Mock(return_value=Mock(json=lambda: response))
        with patch.object(m, "load_preferences",return_value={"project_id":project}):
            rows=cli.list_project_conversations(35,max_pages=1,page_timeout=6.0)
        self.assertEqual([r["id"] for r in rows],["chat-001"])
        cli.request.assert_called_once()
        self.assertEqual(cli.request.call_args.kwargs["timeout"],6.0)
        cli.target_project_id.assert_not_called()

    def test_complete_listing_still_validates_project(self):
        m=backend_module()
        cli=object.__new__(m.ChatGPTWebClient)
        project="g-p-"+"b"*32
        cli.target_project_id=Mock(return_value=project)
        cli.request=Mock(return_value=Mock(json=lambda:{
            "items":[{"id":"good","gizmo_id":project},
                     {"id":"wrong","gizmo_id":"g-p-"+"c"*32}],
            "cursor":None,
        }))
        with patch.object(m,"load_preferences",return_value={"project_id":"g-p-"+"a"*32}):
            rows=cli.list_project_conversations(20)
        self.assertEqual([r["id"] for r in rows],["good"])
        cli.target_project_id.assert_called_once()


if __name__ == "__main__":
    unittest.main()
