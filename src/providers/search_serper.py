"""
Serper.dev provider — 2,500 free searches, one-time, no card required.

Alternative to SerpApi with the same role: PAA/related-search expansion for
Module A, and independent search-evidence for Module B's citation checks.
"""
from __future__ import annotations

import requests

from .base import ProviderError, QuotaExceededError

API_URL = "https://google.serper.dev/search"
TIMEOUT_S = 20


class SerperProvider:
    name = "serper"

    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()

    def search(self, query: str, api_key: str, location: str = "") -> dict:
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
        payload = {"q": query}
        if location:
            payload["location"] = location
        try:
            resp = self.session.post(API_URL, headers=headers, json=payload, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise ProviderError(f"Serper request failed: {e}") from e

        if resp.status_code == 429:
            raise QuotaExceededError(f"Serper rate limit / free-credit exhausted: {resp.text[:300]}")
        if resp.status_code >= 400:
            body = resp.text[:300]
            if "credit" in body.lower():
                raise QuotaExceededError(f"Serper credit exhausted: {body}")
            raise ProviderError(f"Serper error {resp.status_code}: {body}")

        data = resp.json()
        paa = [item.get("question", "") for item in data.get("peopleAlsoAsk", [])]
        related = [item.get("query", "") for item in data.get("relatedSearches", [])]
        organic = [
            {"title": item.get("title", ""), "link": item.get("link", ""), "snippet": item.get("snippet", "")}
            for item in data.get("organic", []) if item.get("link")
        ]
        organic_urls = [o["link"] for o in organic]
        return {
            "people_also_ask": paa, "related_searches": related,
            "organic_urls": organic_urls, "organic_results": organic, "raw": data,
        }

    def validate_key(self, api_key: str) -> bool:
        try:
            self.search("test", api_key)
            return True
        except QuotaExceededError:
            return True  # key is valid, just out of credit
        except ProviderError:
            return False
