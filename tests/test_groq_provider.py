import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.providers.groq_chatgpt import GroqChatGPTProvider
from src.providers.base import ProviderResponse, QuotaExceededError


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))
        self.headers = {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(200, {"data": []}, "ok")


def test_groq_provider_parses_openai_compatible_response():
    session = FakeSession([
        FakeResponse(200, {
            "choices": [{
                "message": {"content": "Acme Fund is mentioned. See https://acme.example.com/guide"}
            }]
        }, "ok")
    ])
    provider = GroqChatGPTProvider(session=session)
    response = provider.generate("hello", "gsk-test")

    assert isinstance(response, ProviderResponse)
    assert response.model == "openai/gpt-oss-120b"
    assert response.cited_urls == ["https://acme.example.com/guide"]
    assert response.text.startswith("Acme Fund")
    assert session.calls[0][1].endswith("/chat/completions")
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer gsk-test"


def test_groq_provider_raises_quota_error_on_429(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    session = FakeSession([
        FakeResponse(429, {"error": "rate limit"}, "rate limit"),
        FakeResponse(429, {"error": "rate limit"}, "rate limit"),
        FakeResponse(429, {"error": "rate limit"}, "rate limit"),
    ])
    provider = GroqChatGPTProvider(session=session)

    try:
        provider.generate("hello", "gsk-test")
        assert False, "expected QuotaExceededError"
    except QuotaExceededError:
        pass


def test_groq_provider_validates_with_models_endpoint():
    session = FakeSession([])
    provider = GroqChatGPTProvider(session=session)
    assert provider.validate_key("gsk-test") is True
    assert session.calls[0][0] == "GET"
    assert session.calls[0][1].endswith("/models")
