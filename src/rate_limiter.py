"""
rate_limiter.py — sliding-window rate limiting that respects BOTH requests
and tokens, per minute AND per day, using the provider's own reported token
usage rather than guessing.

Why this exists: a fixed "sleep N seconds between calls" pacer (the
original design) only defends against the requests-per-minute limit. Groq's
free gpt-oss-120b tier is capped at 30 requests/minute AND 8,000
tokens/minute — at 30 req/min with even ~300 tokens per call (a grounded
prompt plus a few hundred output tokens easily exceeds this), you blow the
TPM budget in well under a minute even while comfortably inside the RPM
budget. The fix isn't a longer fixed delay (that just wastes time when
responses are short); it's tracking a real rolling window of both requests
and tokens and pacing dynamically against whichever budget is tighter for
this response.

Each provider+key gets its own limiter (limits are typically per-key, not
per-app), created lazily by RateLimiterRegistry. Daily (RPD/TPD) counters
are tracked in-memory for the lifetime of the process — they are a soft,
session-scoped safety net, not a persisted cross-restart guarantee. The
per-minute sliding window is the primary, reliable defense against 429s;
daily budgets mainly help decide when to proactively stop rather than
waiting for Groq/Google to reject an already-spent call.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


def estimate_tokens(text: str) -> int:
    """Rough, provider-agnostic estimate (~4 chars/token for English) used
    only BEFORE a call, to decide whether to wait — the real count from the
    provider's own usage report replaces this estimate immediately after."""
    return max(1, len(text or "") // 4)


@dataclass
class RateLimiter:
    name: str
    rpm: Optional[int] = None
    tpm: Optional[int] = None
    rpd: Optional[int] = None
    tpd: Optional[int] = None
    _minute_events: deque = field(default_factory=deque)   # [(timestamp, tokens)]
    _day_start: Optional[float] = None
    _day_requests: int = 0
    _day_tokens: int = 0

    def _prune_minute_window(self, now: float):
        while self._minute_events and now - self._minute_events[0][0] > 60:
            self._minute_events.popleft()

    def _reset_day_if_needed(self, now: float):
        if self._day_start is None or now - self._day_start > 86400:
            self._day_start = now
            self._day_requests = 0
            self._day_tokens = 0

    def seconds_until_next_slot(self, estimated_tokens: int = 0, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        self._prune_minute_window(now)
        wait = 0.0
        if self.rpm and len(self._minute_events) >= self.rpm:
            oldest_ts = self._minute_events[0][0]
            wait = max(wait, 60.0 - (now - oldest_ts) + 0.1)
        if self.tpm:
            minute_tokens = sum(t for _, t in self._minute_events)
            if minute_tokens + estimated_tokens > self.tpm and self._minute_events:
                oldest_ts = self._minute_events[0][0]
                wait = max(wait, 60.0 - (now - oldest_ts) + 0.1)
        return wait

    def wait_if_needed(self, estimated_tokens: int = 0, log=None, sleep_fn=time.sleep, now: Optional[float] = None):
        wait = self.seconds_until_next_slot(estimated_tokens, now=now)
        if wait > 0:
            if log:
                log(f"{self.name}: pacing for free-tier RPM/TPM — waiting {wait:.1f}s before the next call.")
            sleep_fn(wait)

    def daily_budget_exceeded(self, estimated_tokens: int = 0, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.time()
        self._reset_day_if_needed(now)
        if self.rpd is not None and self._day_requests + 1 > self.rpd:
            return True
        if self.tpd is not None and self._day_tokens + estimated_tokens > self.tpd:
            return True
        return False

    def record(self, tokens_used: int, now: Optional[float] = None):
        now = now if now is not None else time.time()
        self._prune_minute_window(now)
        self._reset_day_if_needed(now)
        self._minute_events.append((now, tokens_used))
        self._day_requests += 1
        self._day_tokens += tokens_used

    def usage_snapshot(self) -> dict:
        return self.usage_snapshot_at(time.time())

    def usage_snapshot_at(self, now: float) -> dict:
        self._prune_minute_window(now)
        self._reset_day_if_needed(now)
        return {
            "requests_this_minute": len(self._minute_events),
            "tokens_this_minute": sum(t for _, t in self._minute_events),
            "requests_today": self._day_requests,
            "tokens_today": self._day_tokens,
            "rpm_limit": self.rpm, "tpm_limit": self.tpm, "rpd_limit": self.rpd, "tpd_limit": self.tpd,
        }


class RateLimiterRegistry:
    """One limiter per (provider, api_key) pair, created lazily. Limits are
    typically enforced per-key by the provider, so a rotated-to fresh key
    correctly gets a fresh budget rather than inheriting the old key's
    depleted one."""

    def __init__(self):
        self._limiters: dict = {}

    def get(self, provider: str, api_key: str, rpm=None, tpm=None, rpd=None, tpd=None) -> RateLimiter:
        key = (provider, api_key)
        if key not in self._limiters:
            self._limiters[key] = RateLimiter(name=provider, rpm=rpm, tpm=tpm, rpd=rpd, tpd=tpd)
        return self._limiters[key]

    def snapshot(self) -> dict:
        return {f"{provider}:{k[-4:] if k else ''}": lim.usage_snapshot() for (provider, k), lim in self._limiters.items()}
