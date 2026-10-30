"""No-disruption tests for the periodic ChatGPT Web recovery policy."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent.parent / "maintenance/watchdog.py"
spec = importlib.util.spec_from_file_location("chatgpt_web_watchdog", SOURCE)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.status = Path(self.temp.name) / "status.json"
        self.patches = [
            patch.object(watchdog, "STATE", self.status),
            patch.object(watchdog, "active", return_value=True),
            patch.object(watchdog, "browser_ready", return_value=True),
            patch.object(watchdog, "daemon_ready", return_value=True),
            patch.object(watchdog, "busy_threads", return_value=False),
            patch.object(watchdog, "busy_image_jobs", return_value=False),
            patch.object(watchdog, "cmd"),
            patch.object(watchdog.time, "time", return_value=10000),
        ]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)

    def state(self, **kwargs):
        self.status.write_text(json.dumps(kwargs))

    def test_healthy_does_not_restart(self):
        result = watchdog.run_once()
        self.assertTrue(result["daemon_healthy"])
        self.assertTrue(result["browser_healthy"])
        self.mocks[6].assert_not_called()

    def test_single_failure_never_restarts(self):
        self.mocks[2].return_value = False
        watchdog.run_once()
        self.mocks[6].assert_not_called()

    def test_active_conversation_defers_browser_restart(self):
        self.mocks[2].return_value = False
        self.mocks[4].return_value = True
        self.state(browser_failures=2)
        watchdog.run_once()
        self.mocks[6].assert_not_called()

    def test_unknown_thread_state_defers_restart(self):
        self.mocks[2].return_value = False
        self.mocks[4].return_value = None
        self.state(browser_failures=2)
        watchdog.run_once()
        self.mocks[6].assert_not_called()

    def test_bounded_recovery_after_three_failures(self):
        self.mocks[2].return_value = False
        self.state(browser_failures=2)
        self.mocks[6].return_value.returncode = 0
        watchdog.run_once()
        self.mocks[6].assert_called_once_with(
            "systemctl", "--user", "restart",
            watchdog.BROWSER, timeout=45)
        self.assertEqual(json.loads(self.status.read_text())[
            "browser_last_restart"], 10000)

    def test_active_image_job_defers_browser_restart(self):
        self.mocks[2].return_value = False
        self.mocks[5].return_value = True
        self.state(browser_failures=2)
        watchdog.run_once()
        self.mocks[6].assert_not_called()

    def test_unknown_image_job_state_defers_browser_restart(self):
        self.mocks[2].return_value = False
        self.mocks[5].return_value = None
        self.state(browser_failures=2)
        watchdog.run_once()
        self.mocks[6].assert_not_called()

    def test_cooldown_prevents_restart_loop(self):
        self.mocks[2].return_value = False
        self.state(browser_failures=8, browser_last_restart=9900)
        watchdog.run_once()
        self.mocks[6].assert_not_called()

    def test_browser_failure_blocks_daemon_restart(self):
        self.mocks[2].return_value = False
        self.mocks[3].return_value = False
        self.state(browser_failures=1, daemon_failures=2)
        watchdog.run_once()
        self.mocks[6].assert_not_called()


if __name__ == "__main__":
    unittest.main()
