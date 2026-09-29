"""
OpenAI provider — second always-on model, gated behind an explicit opt-in.

OpenAI has no default free tier. It does run an opt-in program: enabling
data-sharing on a project grants a real daily free token allowance. This
means your prompts/outputs may be used for training — fine for a personal
or demo GEO run, wrong for confidential client data. The UI must show this
tradeoff and require an explicit checkbox; this module itself trusts its
caller (`opted_in`) and refuses to run without it, as a second guard.
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

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT_S = 30
MAX_RETRIES = 3          # short retries for transient 429/5xx before giving up on this cell
RETRY_BASE_DELAY_S = 3.0  # 3s, 6s, 12s backoff


class OptInRequiredError(ProviderError):
    """Raised if a caller tries to use OpenAI without explicit opt-in to the
    free data-sharing tier. Never bypass this check silently."""


class OpenAIProvider(BaseProvider):
    name = "ChatGPT"

    def __init__(self, model: str = None, session: requests.Session = None):
        self.model = model or DEFAULT_MODEL
        self.session = session or requests.Session()

    def generate(self, prompt: str, api_key: str, opted_in: bool = False) -> ProviderResponse:
        if not opted_in:
            raise OptInRequiredError(
                "OpenAI's free tier requires opting into data-sharing "
                "(prompts/outputs may be used for training). Not enabled for this run."
            )
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1024,
        }
        try:
            resp = request_with_retry(
                self.session, "POST", API_URL, headers=headers, json=payload, timeout=TIMEOUT_S,
                max_retries=MAX_RETRIES, base_delay_s=RETRY_BASE_DELAY_S,
            )
        except requests.RequestException as e:
            raise ProviderError(redact_secret(f"OpenAI request failed: {e}", api_key)) from e

        if resp.status_code == 429:
            raise QuotaExceededError(redact_secret(f"OpenAI rate limit / quota exceeded: {resp.text[:300]}", api_key))
        if resp.status_code == 402:
            raise InsufficientCreditError(redact_secret(f"OpenAI billing issue: {resp.text[:300]}", api_key))
        if resp.status_code >= 400:
            body = resp.text[:300]
            if "insufficient_quota" in body or "rate_limit" in body:
                raise QuotaExceededError(redact_secret(f"OpenAI quota error ({resp.status_code}): {body}", api_key))
            raise ProviderError(redact_secret(f"OpenAI error {resp.status_code}: {body}", api_key))

        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ProviderError(redact_secret(f"Unexpected OpenAI response shape: {e}", api_key)) from e

        return ProviderResponse(text=text, cited_urls=extract_urls(text), model=self.model, raw=data)

    def validate_key(self, api_key: str) -> bool:
        try:
            resp = self.session.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False
