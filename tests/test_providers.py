import unittest
from unittest.mock import patch

from openinstruct.providers import LMStudioProvider, OllamaProvider


def fake_json_request(method, url, payload=None, timeout=120):
    if method == "GET" and url.endswith("/api/tags"):
        return {"models": [{"name": "qwen2.5-coder:7b"}]}
    if method == "POST" and url.endswith("/api/chat"):
        return {"message": {"role": "assistant", "content": "hello from ollama"}}
    if method == "GET" and url.endswith("/v1/models"):
        return {"data": [{"id": "local-model"}]}
    if method == "POST" and url.endswith("/v1/chat/completions"):
        return {"choices": [{"message": {"role": "assistant", "content": "hello from lmstudio"}}]}
    raise AssertionError(f"Unexpected request: {method} {url}")


class ProviderTests(unittest.TestCase):
    @patch("openinstruct.providers._json_request", side_effect=fake_json_request)
    def test_ollama_provider(self, _mock_request) -> None:
        provider = OllamaProvider("http://127.0.0.1:11434")
        self.assertEqual(provider.list_models(), ["qwen2.5-coder:7b"])
        reply = provider.chat([{"role": "user", "content": "hi"}], "qwen2.5-coder:7b")
        self.assertEqual(reply, "hello from ollama")

    @patch("openinstruct.providers._json_request", side_effect=fake_json_request)
    def test_lmstudio_provider(self, _mock_request) -> None:
        provider = LMStudioProvider("http://127.0.0.1:1234")
        self.assertEqual(provider.list_models(), ["local-model"])
        reply = provider.chat([{"role": "user", "content": "hi"}], "local-model")
        self.assertEqual(reply, "hello from lmstudio")


if __name__ == "__main__":
    unittest.main()
