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
