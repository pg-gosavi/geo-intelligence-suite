"""
Key rotation + validation for every provider's key pool.

A "key pool" is a comma-separated list of API keys for one provider. Keys are
tried in order; a quota/429 error advances to the next key. When every key in
a pool is exhausted, the pool reports exhausted=True and the run should pause
(state_store.PAUSED_AWAITING_KEY) rather than lose partial results.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional


def mask_key(key: str) -> str:
    """Mask a key to its last 4 characters for display/logging, e.g. '****ab12'."""
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 4 else key
    return "*" * max(0, len(key) - len(tail)) + tail


@dataclass
class KeyStatus:
    key: str
    valid: Optional[bool] = None  # None = not yet validated
    exhausted: bool = False
    last_error: str = ""

    @property
    def masked(self) -> str:
        return mask_key(self.key)


@dataclass
class KeyPool:
    provider: str
    keys: list = field(default_factory=list)  # list[KeyStatus]
    _cursor: int = 0

    @classmethod
    def from_csv(cls, provider: str, csv_value: str) -> "KeyPool":
        keys = [k.strip() for k in (csv_value or "").split(",") if k.strip()]
        return cls(provider=provider, keys=[KeyStatus(key=k) for k in keys])

    def __len__(self):
        return len(self.keys)

    def has_keys(self) -> bool:
        return len(self.keys) > 0

    def current(self) -> Optional[KeyStatus]:
        live = [k for k in self.keys if not k.exhausted]
        if not live:
            return None
        if self._cursor >= len(live):
            self._cursor = 0
        return live[self._cursor]

    def rotate(self, error: str = "") -> Optional[KeyStatus]:
        """Mark the current key exhausted and advance. Returns the new current key, or
        None if the whole pool is exhausted."""
        cur = self.current()
        if cur is not None:
            cur.exhausted = True
            cur.last_error = error
        self._cursor = 0
        return self.current()

    def reset_exhausted(self):
        """Clear every key's exhausted flag (but keep .valid/.last_error history
        for display). Use this after waiting out a transient per-minute rate
        limit — free-tier quotas usually reset within a minute, but a key
        marked exhausted stays that way for the rest of the app session
        otherwise, even after the underlying quota has actually recovered.
        Re-pasting the identical key string does NOT do this automatically
        (it's already in the pool), which is why this exists as an explicit
        action."""
        for ks in self.keys:
            ks.exhausted = False
        self._cursor = 0

    def add_key(self, key: str):
        if key and key not in [k.key for k in self.keys]:
            self.keys.append(KeyStatus(key=key))

    def exhausted(self) -> bool:
        return self.has_keys() and all(k.exhausted for k in self.keys)

    def validate_all(self, validate_fn: Callable[[str], bool]):
        """Run a cheap validation call against each key; sets .valid on each."""
        for ks in self.keys:
            try:
                ks.valid = bool(validate_fn(ks.key))
            except Exception as e:  # noqa: BLE001
                ks.valid = False
                ks.last_error = str(e)

    def status_table(self):
        return [
            {"provider": self.provider, "key": ks.masked, "valid": ks.valid, "exhausted": ks.exhausted}
            for ks in self.keys
        ]


class KeyManager:
    """Holds one KeyPool per provider. Keys can come from pasted UI input or .env."""

    PROVIDERS = ["groq", "gemini", "openai", "claude", "perplexity", "serpapi", "serper", "youtube"]
    ENV_VAR = {
        "groq": "GROQ_API_KEYS",
        "gemini": "GEMINI_API_KEYS",
        "openai": "OPENAI_API_KEYS",
        "claude": "ANTHROPIC_API_KEYS",
        "perplexity": "PERPLEXITY_API_KEYS",
        "serpapi": "SERPAPI_API_KEYS",
        "serper": "SERPER_API_KEYS",
        "youtube": "YOUTUBE_API_KEYS",
    }

    def __init__(self):
        self.pools: dict[str, KeyPool] = {p: KeyPool(provider=p) for p in self.PROVIDERS}

    def load_from_env(self, env: Optional[dict] = None):
        env = env if env is not None else os.environ
        for provider, var in self.ENV_VAR.items():
            csv_value = env.get(var, "")
            if csv_value:
                self.pools[provider] = KeyPool.from_csv(provider, csv_value)

    def set_keys(self, provider: str, csv_value: str):
        self.pools[provider] = KeyPool.from_csv(provider, csv_value)

    def pool(self, provider: str) -> KeyPool:
        return self.pools.setdefault(provider, KeyPool(provider=provider))

    def current_key(self, provider: str) -> Optional[str]:
        cur = self.pool(provider).current()
        return cur.key if cur else None

    def rotate(self, provider: str, error: str = "") -> Optional[str]:
        nxt = self.pool(provider).rotate(error)
        return nxt.key if nxt else None

    def reset_exhausted(self, provider: Optional[str] = None):
        """Reset one provider's pool, or every pool if provider is None. Call
        this after waiting for a free-tier per-minute/per-day quota to clear,
        so the same key(s) can be tried again without restarting the app."""
        providers = [provider] if provider else list(self.pools.keys())
        for p in providers:
            self.pool(p).reset_exhausted()

    def is_exhausted(self, provider: str) -> bool:
        return self.pool(provider).exhausted()

    def any_configured(self, provider: str) -> bool:
        return self.pool(provider).has_keys()
