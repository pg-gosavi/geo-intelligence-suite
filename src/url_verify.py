"""
Independent verification of URLs an LLM claims as sources, plus
classification into the exact status vocabulary used by the sample workbook.

Citation URLs are never "real-time grounded" on free tiers, and LLMs asked
for source URLs will sometimes hallucinate them. So: never present an
unverified URL as fact. Every URL is checked with an HTTP HEAD request
(GET fallback if HEAD isn't allowed) and a short timeout before being
trusted, and only verified links are ever written into a "Cited URLs" cell.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from .schema import (
    SOURCE_STATUS_DOMAINS_ONLY,
    SOURCE_STATUS_EXACT_URLS,
    SOURCE_STATUS_NO_URLS_TEXT,
)

TIMEOUT_S = 6
USER_AGENT = "GEOIntelligenceSuite/1.0 (+citation-verification)"


@dataclass
class UrlCheck:
    url: str
    reachable: bool
    status_code: int | None
    method: str  # "HEAD", "GET", or "error"
    error: str = ""


def is_homepage(url: str) -> bool:
    """True if the URL points at a bare domain root, no meaningful path."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    path = (parsed.path or "").rstrip("/")
    return path == "" and not parsed.query


def verify_url(url: str, session: requests.Session | None = None) -> UrlCheck:
    """HEAD first (cheap); fall back to a GET if the server rejects HEAD
    (405/501) or times out oddly. Any exception is treated as unreachable —
    never assume a URL is good just because we couldn't disprove it."""
    session = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = session.head(url, timeout=TIMEOUT_S, headers=headers, allow_redirects=True)
        if resp.status_code in (405, 501):
            resp = session.get(url, timeout=TIMEOUT_S, headers=headers, allow_redirects=True, stream=True)
            method = "GET"
        else:
            method = "HEAD"
        return UrlCheck(url=url, reachable=(resp.status_code < 400), status_code=resp.status_code, method=method)
    except requests.RequestException as e:
        try:
            resp = session.get(url, timeout=TIMEOUT_S, headers=headers, allow_redirects=True, stream=True)
            return UrlCheck(url=url, reachable=(resp.status_code < 400), status_code=resp.status_code, method="GET")
        except requests.RequestException as e2:
            return UrlCheck(url=url, reachable=False, status_code=None, method="error", error=str(e2) or str(e))


def verify_urls(urls: list, session: requests.Session | None = None) -> list:
    session = session or requests.Session()
    return [verify_url(u, session=session) for u in dict.fromkeys(urls)]  # de-dupe, keep order


def classify_citation(urls: list, checks: list = None, session: requests.Session | None = None):
    """
    Given the raw URLs a model returned (and optionally pre-computed
    UrlCheck results), independently verify them and return:
        (cited_urls_cell: str, source_status: str, supporting_domains: str)
    following the sample workbook's own vocabulary exactly.
    """
    if checks is None:
        checks = verify_urls(urls, session=session)

    verified_non_homepage = [c.url for c in checks if c.reachable and not is_homepage(c.url)]
    verified_any = [c.url for c in checks if c.reachable]

    domains = []
    for u in urls:
        try:
            d = urlparse(u).netloc.replace("www.", "")
        except ValueError:
            d = ""
        if d and d not in domains:
            domains.append(d)
    domains_cell = ", ".join(domains)

    if verified_non_homepage:
        return " | ".join(verified_non_homepage), SOURCE_STATUS_EXACT_URLS, domains_cell
    if verified_any or domains:
        return SOURCE_STATUS_NO_URLS_TEXT, SOURCE_STATUS_DOMAINS_ONLY, domains_cell
    return SOURCE_STATUS_NO_URLS_TEXT, SOURCE_STATUS_DOMAINS_ONLY, ""
