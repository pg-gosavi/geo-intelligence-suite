import os

from src.providers.gemini import GeminiProvider


class Resp:
    def __init__(self, status_code=429, headers=None, payload=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else {"error": {"message": "RESOURCE_EXHAUSTED"}}
        self.text = text or "RESOURCE_EXHAUSTED"

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)

    def get(self, *args, **kwargs):
        return Resp(status_code=200)


def test_retry_after_is_parsed_from_header():
    resp = Resp(headers={"Retry-After": "17"})
    assert GeminiProvider._retry_after_seconds(resp) == 17.0


def test_retry_delay_is_parsed_from_google_error_details():
    resp = Resp(payload={"error": {"details": [{"retryDelay": "12s"}]}})
    assert GeminiProvider._retry_after_seconds(resp) == 12.0


def test_gemini_provider_uses_configurable_flash_lite_default(monkeypatch):
    monkeypatch.setattr("src.providers.gemini.DEFAULT_MODEL", "gemini-3.5-flash-lite")
    provider = GeminiProvider(session=FakeSession([]))
    assert provider.model == "gemini-3.5-flash-lite"
