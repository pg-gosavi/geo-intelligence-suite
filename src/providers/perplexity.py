"""
Perplexity (Sonar API) provider — optional/pluggable, off by default.

Same treatment as Claude: no ongoing free tier, only small one-time trial
credits for new accounts. Wired for when a user has trial/paid credit, but
disabled by default so a demo run doesn't silently burn it. Degrades to
"Not tested" on InsufficientCreditError.
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

DEFAULT_MODEL = os.environ.get("PERPLEXITY_MODEL", "sonar")
API_URL = "https://api.perplexity.ai/chat/completions"
TIMEOUT_S = 30
MAX_RETRIES = 3          # short retries for transient 429/5xx before giving up on this cell
RETRY_BASE_DELAY_S = 3.0  # 3s, 6s, 12s backoff


class PerplexityProvider(BaseProvider):
    name = "Perplexity"

    def __init__(self, model: str = None, session: requests.Session = None):
        self.model = model or DEFAULT_MODEL
        self.session = session or requests.Session()

    def generate(self, prompt: str, api_key: str) -> ProviderResponse:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        try:
            resp = request_with_retry(
                self.session, "POST", API_URL, headers=headers, json=payload, timeout=TIMEOUT_S,
                max_retries=MAX_RETRIES, base_delay_s=RETRY_BASE_DELAY_S,
            )
        except requests.RequestException as e:
            raise ProviderError(redact_secret(f"Perplexity request failed: {e}", api_key)) from e

        if resp.status_code == 429:
            raise QuotaExceededError(redact_secret(f"Perplexity rate limit / quota exceeded: {resp.text[:300]}", api_key))
        if resp.status_code == 402:
            raise InsufficientCreditError(redact_secret(f"Perplexity billing issue: {resp.text[:300]}", api_key))
        if resp.status_code >= 400:
            body = resp.text[:300]
            if "credit" in body.lower() or "insufficient" in body.lower():
                raise InsufficientCreditError(redact_secret(f"Perplexity insufficient credit ({resp.status_code}): {body}", api_key))
            raise ProviderError(redact_secret(f"Perplexity error {resp.status_code}: {body}", api_key))

        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ProviderError(redact_secret(f"Unexpected Perplexity response shape: {e}", api_key)) from e

        # Perplexity's Sonar models often return their own citations array.
        citations = data.get("citations") or []
        urls = list(citations) + extract_urls(text)
        seen, deduped = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        return ProviderResponse(text=text, cited_urls=deduped, model=self.model, raw=data)

    def validate_key(self, api_key: str) -> bool:
        try:
            resp = self.generate("Reply with the single word: ok", api_key)
            return bool(resp.text)
        except InsufficientCreditError:
            return False
        except ProviderError:
            return False
