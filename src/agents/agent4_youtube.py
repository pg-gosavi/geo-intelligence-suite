"""
Agent 4 — Video Intelligence (Module B).

Queries the YouTube Data API v3 (search.list) with the same Test Set prompts
to fetch the top 5 video results (title/link) for video-SEO analysis, as
specified in the PRD.

This is genuinely optional: it needs its own API key (a free Google Cloud
API key, ~100 search units/day of quota at 100 units per search.list call),
it is not one of the five LLM/search providers the free-tier matrix covers,
and the reference workbook has no video sheet — so its output is not part
of the xlsx schema contract and is stored only in the run's SQLite state for
now, ready to surface in a future export. Skips cleanly (no crash, no
"Not tested" spam) if no key is configured.
"""
from __future__ import annotations

import requests

from ..providers.base import ProviderError, QuotaExceededError

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
TIMEOUT_S = 15
MODULE = "agent4_youtube"


def fetch_top_videos(prompt: str, api_key: str, max_results: int = 5, session: requests.Session = None) -> list:
    session = session or requests.Session()
    params = {
        "part": "snippet", "q": prompt, "type": "video",
        "maxResults": max_results, "key": api_key,
    }
    try:
        resp = session.get(SEARCH_URL, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise ProviderError(f"YouTube search failed: {e}") from e

    if resp.status_code == 403 and "quota" in resp.text.lower():
        raise QuotaExceededError(f"YouTube API daily quota exceeded: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise ProviderError(f"YouTube API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    videos = []
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        title = item.get("snippet", {}).get("title", "")
        if vid:
            videos.append({"title": title, "url": f"https://www.youtube.com/watch?v={vid}"})
    return videos


def run_agent4(store, run_id: str, test_set_rows: list, api_key: str = "", log=None, session=None) -> dict:
    results = {}
    if not api_key:
        if log:
            log("No YouTube API key configured — video intelligence skipped (optional module).")
        return results

    from .. import state_store as ss
    unit_keys = [str(row["num"]) for row in test_set_rows]
    ss.ensure_units_pending(store, run_id, MODULE, unit_keys)

    for row in test_set_rows:
        uk = str(row["num"])
        existing = store.get_unit(run_id, MODULE, uk)
        if existing and existing["status"] == ss.DONE:
            results[row["num"]] = existing["result"]
            continue
        try:
            videos = fetch_top_videos(row["conversational_prompt"], api_key, session=session)
            store.upsert_unit(run_id, MODULE, uk, ss.DONE, result={"videos": videos})
            results[row["num"]] = {"videos": videos}
        except QuotaExceededError as e:
            store.upsert_unit(run_id, MODULE, uk, ss.FAILED_QUOTA, error=str(e))
            if log:
                log(f"YouTube quota exhausted at prompt #{row['num']} — remaining rows skipped this run.")
            break
        except ProviderError as e:
            store.upsert_unit(run_id, MODULE, uk, ss.FAILED_OTHER, error=str(e))
            if log:
                log(f"YouTube lookup failed for prompt #{row['num']}: {e}")
    return results
