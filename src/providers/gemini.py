"""
Google Gemini provider for the GEO Intelligence Suite.

Uses Google's Gemini REST API directly. The model is configurable through
GEMINI_MODEL and defaults to Gemini 3.5 Flash-Lite, a current fast/low-cost
Gemini model suitable for higher-throughput text workloads.

Rate-limit handling is deliberately provider-specific:
- the caller controls a Gemini-only pacing delay (GEMINI_MIN_INTERVAL_S);
- transient 429/5xx responses are retried with exponential backoff;
- when Google provides Retry-After or a structured retryDelay hint, that
  server-provided wait is preferred instead of blindly retrying immediately.

API keys are always sent in the x-goog-api-key header, never in the URL.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests

from .base import (
    BaseProvider,
    InsufficientCreditError,
    ProviderError,
    ProviderResponse,
    QuotaExceededError,
    extract_urls,
    redact_secret,
)

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT_S = float(os.environ.get("GEMINI_TIMEOUT_S", "45"))
MAX_RETRIES = int(os.environ.get("GEMINI_MAX_RETRIES", "3"))
RETRY_BASE_DELAY_S = float(os.environ.get("GEMINI_RETRY_BASE_DELAY_S", "2.0"))
RETRY_MAX_DELAY_S = float(os.environ.get("GEMINI_RETRY_MAX_DELAY_S", "90.0"))


class GeminiProvider(BaseProvider):
    name = "Gemini"

    def __init__(self, model: Optional[str] = None, session: requests.Session = None):
        self.model = model or DEFAULT_MODEL
        self.session = session or requests.Session()

    def _endpoint(self) -> str:
        return f"{API_BASE}/{self.model}:generateContent"

    @staticmethod
    def _retry_after_seconds(resp: requests.Response) -> Optional[float]:
        """Extract Google's recommended retry delay from header/body when present."""
        headers = getattr(resp, "headers", {}) or {}
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass

        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            return None

        # Google APIs can return structured ErrorInfo/RetryInfo details.
        details = (body.get("error") or {}).get("details") or []
        for detail in details:
            retry_delay = detail.get("retryDelay") if isinstance(detail, dict) else None
            if retry_delay:
                m = re.match(r"^([0-9]+(?:\.[0-9]+)?)s$", str(retry_delay).strip())
                if m:
                    return max(0.0, float(m.group(1)))

        # Some proxies/services surface a textual retry delay.
        text = str((body.get("error") or {}).get("message", ""))
        m = re.search(r"retry.*?(?:after|in)\s+(\d+(?:\.\d+)?)\s*s", text, re.I)
        if m:
            return max(0.0, float(m.group(1)))
        return None

    def _post_with_retry(self, headers: dict, payload: dict) -> requests.Response:
        last_resp = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.session.post(
                    self._endpoint(),
                    headers=headers,
                    json=payload,
                    timeout=TIMEOUT_S,
                )
            except requests.RequestException:
                if attempt >= MAX_RETRIES:
                    raise
                delay = min(RETRY_BASE_DELAY_S * (2 ** attempt), RETRY_MAX_DELAY_S)
                time.sleep(delay)
                continue

            last_resp = resp
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt >= MAX_RETRIES:
                return resp

            server_delay = self._retry_after_seconds(resp)
            delay = server_delay if server_delay is not None else (RETRY_BASE_DELAY_S * (2 ** attempt))
            time.sleep(min(max(0.0, delay), RETRY_MAX_DELAY_S))

        return last_resp

    def generate(self, prompt: str, api_key: str) -> ProviderResponse:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1024,
            },
        }
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }
        try:
            resp = self._post_with_retry(headers, payload)
        except requests.RequestException as e:
            raise ProviderError(redact_secret(f"Gemini request failed: {e}", api_key)) from e

        if resp.status_code == 429:
            retry_after = self._retry_after_seconds(resp)
            suffix = f"; retry_after={retry_after:.1f}s" if retry_after is not None else ""
            raise QuotaExceededError(
                redact_secret(f"Gemini rate limit / quota exceeded{suffix}: {resp.text[:300]}", api_key)
            )
        if resp.status_code == 402:
            raise InsufficientCreditError(redact_secret(f"Gemini billing issue: {resp.text[:300]}", api_key))
        if resp.status_code >= 400:
            body = redact_secret(resp.text[:300], api_key)
            if "quota" in body.lower() or "rate" in body.lower() or "resource_exhausted" in body.lower():
                raise QuotaExceededError(f"Gemini quota error ({resp.status_code}): {body}")
            raise ProviderError(f"Gemini error {resp.status_code}: {body}")

        try:
            data = resp.json()
            candidates = data.get("candidates", [])
            text = "".join(
                part.get("text", "")
                for cand in candidates
                for part in cand.get("content", {}).get("parts", [])
            )
        except (ValueError, json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
            raise ProviderError(f"Unexpected Gemini response shape: {e}") from e

        return ProviderResponse(
            text=text, cited_urls=extract_urls(text), model=self.model, raw=data,
            usage={"total_tokens": (data.get("usageMetadata") or {}).get("totalTokenCount"),
                   "prompt_tokens": (data.get("usageMetadata") or {}).get("promptTokenCount"),
                   "completion_tokens": (data.get("usageMetadata") or {}).get("candidatesTokenCount")},
        )

    def validate_key(self, api_key: str) -> bool:
        """Cheap connectivity/key validation against Google's model-list endpoint."""
        try:
            resp = self.session.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": api_key},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False
