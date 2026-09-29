"""
Groq-hosted OpenAI GPT OSS 120B provider.

The application keeps the logical provider name "ChatGPT" so the existing
workbook/UI schema does not need a wholesale rename, but the actual model is
OpenAI's open-weight GPT-OSS 120B served through Groq's OpenAI-compatible API.

Groq endpoint:
    https://api.groq.com/openai/v1/chat/completions

Environment:
    GROQ_API_KEYS=key1,key2
    GROQ_MODEL=openai/gpt-oss-120b
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

DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_BASE = "https://api.groq.com/openai/v1"
API_URL = f"{API_BASE}/chat/completions"
TIMEOUT_S = 60
MAX_RETRIES = 2
RETRY_BASE_DELAY_S = 2.0


class GroqChatGPTProvider(BaseProvider):
    """ChatGPT evaluation slot backed by Groq-hosted GPT-OSS 120B."""

    name = "ChatGPT"

    def __init__(self, model: str = None, session: requests.Session = None):
        self.model = model or DEFAULT_MODEL
        self.session = session or requests.Session()

    def generate(self, prompt: str, api_key: str) -> ProviderResponse:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1024,
        }

        try:
            resp = request_with_retry(
                self.session,
                "POST",
                API_URL,
                headers=headers,
                json=payload,
                timeout=TIMEOUT_S,
                max_retries=MAX_RETRIES,
                base_delay_s=RETRY_BASE_DELAY_S,
            )
        except requests.RequestException as e:
            raise ProviderError(redact_secret(f"Groq request failed: {e}", api_key)) from e

        body = redact_secret(resp.text[:500], api_key)

        if resp.status_code == 429:
            raise QuotaExceededError(f"Groq rate limit / quota exceeded: {body}")
        if resp.status_code == 402:
            raise InsufficientCreditError(f"Groq billing / credit issue: {body}")
        if resp.status_code == 401:
            raise ProviderError(f"Groq authentication failed: {body}")
        if resp.status_code >= 400:
            lowered = body.lower()
            if "rate limit" in lowered or "quota" in lowered or "too many requests" in lowered:
                raise QuotaExceededError(f"Groq quota error ({resp.status_code}): {body}")
            raise ProviderError(f"Groq error {resp.status_code}: {body}")

        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ProviderError(
                redact_secret(f"Unexpected Groq response shape: {e}; body={resp.text[:500]}", api_key)
            ) from e

        usage = data.get("usage", {}) or {}
        return ProviderResponse(
            text=text or "",
            cited_urls=extract_urls(text or ""),
            model=self.model,
            raw=data,
            usage={"total_tokens": usage.get("total_tokens"), "prompt_tokens": usage.get("prompt_tokens"),
                   "completion_tokens": usage.get("completion_tokens")},
        )

    def validate_key(self, api_key: str) -> bool:
        """Validate the Groq key without consuming a generation request."""
        try:
            resp = self.session.get(
                f"{API_BASE}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False
