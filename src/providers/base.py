"""Common provider interface. Every LLM provider module exposes a class with
a single `generate(prompt, api_key) -> ProviderResponse` method, so agents
and tests can swap real providers for fakes without caring which is which.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

URL_RE = re.compile(r"https?://[^\s\)\]\>\"'|]+")


class ProviderError(Exception):
    """Generic, non-retryable provider failure."""


class QuotaExceededError(ProviderError):
    """A 429 / rate-limit / daily-quota error. Callers should rotate to the
    next key in the pool and retry the same unit of work."""


class InsufficientCreditError(ProviderError):
    """One-time trial credit is exhausted (Claude, Perplexity). Callers should
    degrade this provider to 'Not tested' for the rest of the run rather than
    treating it as a hard failure."""


@dataclass
class ProviderResponse:
    text: str
    cited_urls: list = field(default_factory=list)  # URLs the model itself listed as sources
    model: str = ""
    raw: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)  # e.g. {"total_tokens": N} — real usage, when the API reports it


def redact_secret(text: str, secret: str) -> str:
    """Strip a known secret (API key) out of any string before it is ever
    logged, raised, or written to a report. Applied defensively even where a
    key is passed via header rather than URL, in case a lower-level HTTP
    exception ever echoes the full request."""
    if not text or not secret or len(secret) < 4:
        return text
    from .. import key_manager  # local import avoids a circular import at module load time
    return text.replace(secret, key_manager.mask_key(secret))


def extract_urls(text: str) -> list:
    """Pull URLs out of free-form model text, de-duplicated, order-preserved."""
    seen = []
    for m in URL_RE.findall(text or ""):
        cleaned = m.rstrip(".,;:")
        if cleaned not in seen:
            seen.append(cleaned)
    return seen


# Status codes worth retrying before giving up: 429 (rate limit — often a
# short per-minute window on free tiers) and the 5xx family (transient
# server-side overload, e.g. Gemini's "high demand" 503). A few short
# retries here mean a momentary blip doesn't cost you a whole prompt/cell,
# or — worse — get misread as a permanently exhausted key.
RETRYABLE_STATUS_CODES = (429, 500, 502, 503, 504)


def request_with_retry(session, method: str, url: str, max_retries: int = 3,
                        base_delay_s: float = 2.0, retry_statuses=RETRYABLE_STATUS_CODES, **kwargs):
    """POST/GET with short exponential backoff on transient errors. Returns
    the final response regardless of status — the caller still does its own
    429/5xx/error-body classification afterwards. Only retries
    `max_retries` times total; after that, whatever response came back
    (still an error) is returned as-is for normal classification, so a
    truly exhausted key or a persistent outage still surfaces correctly."""
    import time as _time

    last_resp = None
    for attempt in range(max_retries + 1):
        try:
            resp = session.request(method, url, **kwargs) if hasattr(session, "request") else (
                session.post(url, **kwargs) if method.upper() == "POST" else session.get(url, **kwargs)
            )
        except Exception:
            if attempt >= max_retries:
                raise
            _time.sleep(base_delay_s * (2 ** attempt))
            continue
        last_resp = resp
        if resp.status_code not in retry_statuses or attempt >= max_retries:
            return resp
        retry_after = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
        try:
            delay = float(retry_after) if retry_after else base_delay_s * (2 ** attempt)
        except ValueError:
            delay = base_delay_s * (2 ** attempt)
        _time.sleep(min(delay, 30.0))
    return last_resp


class BaseProvider:
    name = "base"

    def generate(self, prompt: str, api_key: str) -> ProviderResponse:
        raise NotImplementedError

    def validate_key(self, api_key: str) -> bool:
        """Cheap call to confirm a key is live. Default: assume valid (subclasses
        that can afford a real cheap ping should override)."""
        return bool(api_key)
