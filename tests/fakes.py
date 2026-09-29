"""Fakes shared across the test suite. No test in this repo makes a real
network call to an LLM, search, or YouTube API — everything here is a
drop-in stand-in with the same interface as the real provider classes."""
from __future__ import annotations

from src.providers.base import InsufficientCreditError, ProviderError, ProviderResponse, QuotaExceededError


class FakeLLMProvider:
    """canned: {prompt: (text, urls)}. quota_after: raise QuotaExceededError
    starting from the Nth call (1-indexed), to exercise rotation/resume."""

    def __init__(self, name="FakeLLM", canned=None, quota_after=None, credit_after=None):
        self.name = name
        self.canned = canned or {}
        self.quota_after = quota_after
        self.credit_after = credit_after
        self.calls = 0

    def generate(self, prompt, api_key, **kwargs):
        self.calls += 1
        if self.quota_after is not None and self.calls >= self.quota_after:
            raise QuotaExceededError(f"{self.name} fake quota exceeded on call {self.calls}")
        if self.credit_after is not None and self.calls >= self.credit_after:
            raise InsufficientCreditError(f"{self.name} fake credit exhausted on call {self.calls}")
        text, urls = self.canned.get(prompt, ("No relevant information found.", []))
        return ProviderResponse(text=text, cited_urls=list(urls), model=self.name)

    def validate_key(self, api_key):
        return bool(api_key)


class FlakyThenRecoverProvider:
    """Fails with QuotaExceededError for every call using `bad_key`, succeeds
    for any other key — used to test key rotation deterministically."""

    def __init__(self, name, canned, bad_key="expired-key"):
        self.name = name
        self.canned = canned
        self.bad_key = bad_key
        self.calls_by_key = {}

    def generate(self, prompt, api_key, **kwargs):
        self.calls_by_key[api_key] = self.calls_by_key.get(api_key, 0) + 1
        if api_key == self.bad_key:
            raise QuotaExceededError("fake rate limit on bad_key")
        text, urls = self.canned.get(prompt, ("No relevant information found.", []))
        return ProviderResponse(text=text, cited_urls=list(urls), model=self.name)

    def validate_key(self, api_key):
        return api_key != self.bad_key


class FakeSearchClient:
    def __init__(self, paa=None, organic_urls=None, organic_results=None):
        self.paa = paa or []
        self.organic_urls = organic_urls or []
        # organic_results: optional [{"title":..., "link":..., "snippet":...}]
        # Defaults to bare titled entries built from organic_urls so existing
        # tests that only pass organic_urls keep working unchanged.
        self.organic_results = organic_results or [
            {"title": u, "link": u, "snippet": ""} for u in self.organic_urls
        ]
        self.calls = 0

    def search(self, query, api_key, location=""):
        self.calls += 1
        return {
            "people_also_ask": list(self.paa),
            "related_searches": [],
            "organic_urls": list(self.organic_urls),
            "organic_results": list(self.organic_results),
        }


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class FakeSession:
    """Minimal requests.Session stand-in for url_verify tests: maps URL ->
    status code (or 'timeout'/'error' sentinel)."""

    def __init__(self, url_status: dict):
        self.url_status = url_status

    def head(self, url, timeout=None, headers=None, allow_redirects=True):
        return self._resolve(url)

    def get(self, url, timeout=None, headers=None, allow_redirects=True, stream=False):
        return self._resolve(url)

    def _resolve(self, url):
        import requests
        status = self.url_status.get(url, 404)
        if status == "timeout":
            raise requests.exceptions.Timeout(f"timeout on {url}")
        if status == "error":
            raise requests.exceptions.ConnectionError(f"connection error on {url}")
        return FakeResponse(status_code=status)
