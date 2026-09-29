"""
Claude provider — optional/pluggable, off by default.

Anthropic's API has no ongoing free tier, only a one-time ~$5 new-account
credit. This module is fully wired for when a user has that credit (or a
paid key) available, but the app defaults this provider to disabled so a
demo run doesn't silently burn a one-time credit. If a call fails with an
insufficient-credit / billing error, the caller (agent3) should catch
InsufficientCreditError and record "Not tested" for the rest of the run
rather than crashing.
"""
from __future__ import annotations

import os

import requests

from .base import (
    BaseProvider,
    InsufficientCreditError,
    ProviderError,
    ProviderResponse,
    QuotaExceededError,
    extract_urls,
    redact_secret,
    request_with_retry,
)

DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT_S = 30
MAX_RETRIES = 3          # short retries for transient 429/5xx before giving up on this cell
RETRY_BASE_DELAY_S = 3.0  # 3s, 6s, 12s backoff


class ClaudeProvider(BaseProvider):
    name = "Claude"

    def __init__(self, model: str = None, session: requests.Session = None):
        self.model = model or DEFAULT_MODEL
        self.session = session or requests.Session()

    def generate(self, prompt: str, api_key: str) -> ProviderResponse:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = request_with_retry(
                self.session, "POST", API_URL, headers=headers, json=payload, timeout=TIMEOUT_S,
                max_retries=MAX_RETRIES, base_delay_s=RETRY_BASE_DELAY_S,
            )
        except requests.RequestException as e:
            raise ProviderError(redact_secret(f"Claude request failed: {e}", api_key)) from e

        if resp.status_code == 429:
            raise QuotaExceededError(redact_secret(f"Claude rate limit / quota exceeded: {resp.text[:300]}", api_key))
        if resp.status_code in (402,):
            raise InsufficientCreditError(redact_secret(f"Claude billing issue: {resp.text[:300]}", api_key))
        if resp.status_code >= 400:
            body = resp.text[:300]
            if "credit balance" in body.lower() or "insufficient" in body.lower():
                raise InsufficientCreditError(redact_secret(f"Claude insufficient credit ({resp.status_code}): {body}", api_key))
            if "rate_limit" in body.lower() or "overloaded" in body.lower():
                raise QuotaExceededError(redact_secret(f"Claude quota/overload error ({resp.status_code}): {body}", api_key))
            raise ProviderError(redact_secret(f"Claude error {resp.status_code}: {body}", api_key))

        data = resp.json()
        try:
            text = "".join(block.get("text", "") for block in data.get("content", []))
        except (KeyError, AttributeError) as e:
            raise ProviderError(redact_secret(f"Unexpected Claude response shape: {e}", api_key)) from e

        return ProviderResponse(text=text, cited_urls=extract_urls(text), model=self.model, raw=data)

    def validate_key(self, api_key: str) -> bool:
        try:
            resp = self.generate("Reply with the single word: ok", api_key)
            return bool(resp.text)
        except InsufficientCreditError:
            return False
        except ProviderError:
            return False
