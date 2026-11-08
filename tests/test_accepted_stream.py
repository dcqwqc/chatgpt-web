"""Stream-loss recovery never starts a new ChatGPT request."""
import unittest

from maintenance.accepted_stream import wait_for_original_final


class AcceptedStreamTests(unittest.TestCase):
    def test_recovers_after_transient_get_and_incomplete_source(self):
        state = {"time": 0.0, "reads": 0, "progress": []}

        def get():
            state["reads"] += 1
            if state["reads"] == 1:
                raise OSError("Temporary failure")
            return {"final": state["reads"] >= 4}

        result = wait_for_original_final(
            get, lambda d: d["final"],
            on_progress=lambda a: state["progress"].append(a),
            clock=lambda: state["time"],
            sleep=lambda sec: state.__setitem__("time", state["time"] + sec),
            timeout_seconds=120, poll_seconds=12,
        )
        self.assertTrue(result["final"])
        self.assertEqual(state["reads"], 4)
        self.assertEqual(state["progress"], [1])

    def test_does_not_replay_when_source_unfinished(self):
        state = {"time": 0, "reads": 0}
        def get():
            state["reads"] += 1
            return {"final": False}
        with self.assertRaises(TimeoutError):
            wait_for_original_final(
                get, lambda d: d["final"],
                clock=lambda: state["time"],
                sleep=lambda sec: state.__setitem__("time", state["time"] + sec),
                timeout_seconds=15, poll_seconds=10,
            )
        self.assertEqual(state["reads"], 3)

    def test_never_hides_nontransient_authentication_error(self):
        def get():
            raise ValueError("Authentication required")
        with self.assertRaisesRegex(ValueError, "Authentication"):
            wait_for_original_final(get, lambda _: True,
                                    transient_errors=(OSError,))

    def test_rate_limit_respects_server_retry_after_without_resend(self):
        state = {"time": 0.0, "reads": 0, "sleeps": []}
        class RateLimited(RuntimeError):
            pass
        def get():
            state["reads"] += 1
            if state["reads"] == 1:
                raise RateLimited("retry after 150 seconds")
            return {"final": True}
        def sleep(seconds):
            state["sleeps"].append(seconds)
            state["time"] += seconds
        result = wait_for_original_final(
            get, lambda d: d["final"], transient_errors=(RateLimited,),
            rate_limit_delay=lambda e: 150 if isinstance(e, RateLimited) else None,
            clock=lambda: state["time"], sleep=sleep,
            timeout_seconds=250, poll_seconds=75,
        )
        self.assertTrue(result["final"])
        self.assertEqual(state["sleeps"], [155])
        self.assertEqual(state["reads"], 2)

    def test_invalid_bounds_fail_closed(self):
        with self.assertRaises(ValueError):
            wait_for_original_final(lambda: {}, lambda _: True, poll_seconds=0)


if __name__ == "__main__":
    unittest.main()
