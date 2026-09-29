"""
Agent 1 — Prompt Discovery & Expansion (Module A, "The Ask").

Given 2-3 seed prompts, a brand, competitors, and a location, produces a
raw candidate list of high-intent prompts by:
  1. Keeping each seed prompt itself.
  2. Expanding via Google "People Also Ask" / related searches (SerpApi or
     Serper — whichever key pool has a live key).
  3. Querying Gemini for historical/frequent related prompts, categorised
     by theme (Travel/Service/Fees/Beginners/etc. per the PRD).

Each result becomes a row shaped for the "Prompt Discovery" sheet; Agent 2
(consolidation) later dedupes and finalises the Test Set.
"""
from __future__ import annotations

import json
import re

from ..providers.base import ProviderError, QuotaExceededError

INTENT_KEYWORDS = [
    ("comparison", ["vs", "versus", "compare", "compared to", "better than", "difference between"]),
    ("fees_pricing", ["fee", "fees", "cost", "expense", "price", "pricing", "charge", "commission", "tax", "taxed"]),
    ("eligibility", ["eligib", "documents", "requirement", "who can", "kyc", "resident"]),
    ("application_process", ["how to", "how do i", "step by step", "process", "start investing", "set up", "apply"]),
    ("problem_discovery", ["risk", "confused", "worried", "problem", "unhappy", "switch", "safe"]),
    ("category_research", ["which app", "which platform", "which companies", "which amc", "trusted"]),
]


def classify_intent(text: str) -> str:
    t = text.lower()
    for label, kws in INTENT_KEYWORDS:
        if any(kw in t for kw in kws):
            return label
    return "recommendation"


def _new_row(seed_theme, source, expanded_query, conversational_prompt, priority="M", notes=""):
    return {
        "seed_theme": seed_theme,
        "source": source,
        "expanded_query": expanded_query,
        "intent_category": classify_intent(conversational_prompt or expanded_query),
        "conversational_prompt": conversational_prompt or expanded_query,
        "priority": priority,
        "include_in_test_set": "No",
        "notes": notes,
        "execution_status": "Not executed",
        "agent1_status": "Generated",
        "agent2_status": "",
        "approved_by": "",
    }


def expand_with_search(seed_prompts, search_client, search_api_key, location, log=None):
    """search_client: object exposing .search(query, api_key, location) -> dict with
    'people_also_ask' and 'related_searches' lists. Any provider error is caught and
    logged — search expansion is a nice-to-have, not a hard dependency."""
    rows = []
    if not search_client or not search_api_key:
        if log:
            log("No search API key configured — skipping PAA/related-search expansion.")
        return rows
    for seed in seed_prompts:
        try:
            result = search_client.search(seed, search_api_key, location=location)
        except QuotaExceededError as e:
            if log:
                log(f"Search quota exhausted while expanding '{seed}': {e}")
            break
        except ProviderError as e:
            if log:
                log(f"Search expansion failed for '{seed}': {e}")
            continue
        for q in result.get("people_also_ask", []):
            if q:
                rows.append(_new_row(seed, "PAA", q, q, priority="M"))
        for q in result.get("related_searches", []):
            if q:
                rows.append(_new_row(seed, "related", q, q, priority="L"))
    return rows


LLM_EXPANSION_PROMPT = """You are helping build a keyword/prompt research list for GEO (Generative Engine
Optimisation) analysis in the "{sector}" sector, region: {location}.

Brand: {brand}
Competitors: {competitors}
Seed prompts:
{seeds}

List 10 additional realistic, conversational, high-intent questions a real
consumer might type into ChatGPT or Gemini when researching this category
(not the brand by name). Cover a spread of categories: beginners,
fees/pricing, comparisons, eligibility, and troubleshooting.

Respond ONLY with a JSON array of strings, no preamble, no markdown fences.
"""


def expand_with_llm(seed_prompts, brand, competitors, location, sector, llm_client, llm_api_key, log=None):
    """llm_client: object exposing .generate(prompt, api_key) -> ProviderResponse."""
    rows = []
    if not llm_client or not llm_api_key:
        if log:
            log("No LLM key configured for database-query expansion — skipping.")
        return rows
    prompt = LLM_EXPANSION_PROMPT.format(
        sector=sector, location=location, brand=brand,
        competitors=", ".join(competitors), seeds="\n".join(f"- {s}" for s in seed_prompts),
    )
    try:
        resp = llm_client.generate(prompt, llm_api_key)
    except QuotaExceededError as e:
        if log:
            log(f"LLM expansion quota exhausted: {e}")
        return rows
    except ProviderError as e:
        if log:
            log(f"LLM expansion failed: {e}")
        return rows

    text = resp.text.strip()
    match = re.search(r"\[.*\]", text, re.S)
    candidates = []
    if match:
        try:
            candidates = json.loads(match.group(0))
        except json.JSONDecodeError:
            candidates = []
    if not candidates:
        # Fallback: one question per non-empty line.
        candidates = [line.strip("-* ").strip() for line in text.splitlines() if line.strip()]

    for q in candidates:
        if isinstance(q, str) and q.strip():
            rows.append(_new_row("llm_database_query", "llm_db", q.strip(), q.strip(), priority="M"))
    return rows


def run_agent1(seed_prompts, brand, competitors, location, sector,
                search_client=None, search_api_key=None,
                llm_client=None, llm_api_key=None, log=None):
    rows = [
        _new_row(s, "seed", s, s, priority="M", notes="")
        for s in seed_prompts
    ]
    rows += expand_with_search(seed_prompts, search_client, search_api_key, location, log=log)
    rows += expand_with_llm(seed_prompts, brand, competitors, location, sector, llm_client, llm_api_key, log=log)
    return rows
