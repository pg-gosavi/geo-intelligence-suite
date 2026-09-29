"""
SerpApi provider — 250 free searches/month, recurring, no card required.

Used for Module A's "People Also Ask" / autosuggest expansion and Module B's
independent search-evidence pass. Google's Custom Search JSON API is closed
to new signups and being discontinued Jan 1 2027, so this (or Serper) is the
default path — never build against the old Google CSE API.
"""
from __future__ import annotations

import requests

from .base import ProviderError, QuotaExceededError

API_URL = "https://serpapi.com/search"
TIMEOUT_S = 20


class SerpApiProvider:
    name = "serpapi"

    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()

    def search(self, query: str, api_key: str, location: str = "") -> dict:
        params = {"q": query, "api_key": api_key, "engine": "google"}
        if location:
            params["location"] = location
        try:
            resp = self.session.get(API_URL, params=params, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise ProviderError(f"SerpApi request failed: {e}") from e

        if resp.status_code == 429:
            raise QuotaExceededError(f"SerpApi rate limit / monthly quota exceeded: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise ProviderError(f"SerpApi error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        if "error" in data:
            err = str(data["error"])
            if "run out of searches" in err.lower() or "quota" in err.lower():
                raise QuotaExceededError(f"SerpApi quota exhausted: {err}")
            raise ProviderError(f"SerpApi error: {err}")

        paa = [item.get("question", "") for item in data.get("related_questions", [])]
        related = [item.get("query", "") for item in data.get("related_searches", [])]
        organic = [
            {"title": item.get("title", ""), "link": item.get("link", ""), "snippet": item.get("snippet", "")}
            for item in data.get("organic_results", []) if item.get("link")
        ]
        organic_urls = [o["link"] for o in organic]
        return {
            "people_also_ask": paa, "related_searches": related,
            "organic_urls": organic_urls, "organic_results": organic, "raw": data,
        }

    def validate_key(self, api_key: str) -> bool:
        try:
            resp = self.session.get(
                "https://serpapi.com/account", params={"api_key": api_key}, timeout=10
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False
