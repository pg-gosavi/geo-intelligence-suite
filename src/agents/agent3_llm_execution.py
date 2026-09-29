"""
Agent 3 — LLM Playground (Module B).

Runs every Test Set prompt against every *active* LLM, atomically persisting
each (prompt x LLM) cell to SQLite the instant it completes. This is the
module the free-tier build's key-rotation/resume feature exists for:

  - A quota/429 error rotates to the next key in that provider's pool and
    retries the same cell.
  - If a provider's whole key pool is exhausted, the run is marked
    `paused_awaiting_key` and execution stops immediately — every already-
    `done` cell stays done, nothing in flight is lost.
  - Resuming (after the user pastes a fresh key) reloads state, skips every
    `done` cell, and continues from the first `pending`/`failed_quota` cell.
  - Non-quota provider errors mark just that cell `failed_other` and
    execution continues — they are not run-pausing conditions.
  - Gemini and Groq use independent pacing delays, so Gemini's tighter RPM
    limit does not unnecessarily slow the Groq path.
  - Claude/Perplexity InsufficientCreditError degrades that provider to
    "Not tested" for the remainder of the run rather than failing the run.
"""
from __future__ import annotations

import json
import re
import time

from ..providers.base import InsufficientCreditError, ProviderError, ProviderResponse, QuotaExceededError
from .. import state_store as ss
from .. import url_verify
from ..schema import (
    ALL_LLMS,
    PROVIDER_DEFAULT_STATUS,
    SOURCE_STATUS_FUTURE_SCOPE,
    SOURCE_STATUS_NOT_TESTED,
)

MODULE = "agent3_llm_execution"


def parse_brand_mentions(response_text: str, entities: list) -> dict:
    """Rank entities by order of first mention in the model's free-text answer.
    Returns {entity: {"mentioned": bool, "position": "#1"/"-"}}."""
    text = response_text or ""
    first_index = {}
    for entity in entities:
        # word-boundary-ish, case-insensitive match on the entity's own name
        pattern = re.escape(entity)
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            first_index[entity] = m.start()
    ranked = sorted(first_index.items(), key=lambda kv: kv[1])
    result = {e: {"mentioned": False, "position": "-"} for e in entities}
    for i, (entity, _) in enumerate(ranked, start=1):
        result[entity] = {"mentioned": True, "position": f"#{i}"}
    return result


def unit_key(prompt_num: int, llm: str) -> str:
    return f"{prompt_num}:{llm}"


GROUNDING_TEMPLATE = """{question}

For additional context, here are real, currently-indexed web sources for this exact query (found via live search just now, not from memory):
{sources_block}

Answer the question directly and naturally, the way you normally would. If one of the sources above genuinely supports a specific claim you make (a brand recommendation, a fee, a feature, an eligibility rule), cite its exact URL from the list. Do not invent, guess, or reconstruct a URL that is not in the list above — if you don't have a real source for a claim, simply don't cite one."""


def build_grounded_prompt(question: str, titled_results: list) -> str:
    """Wrap the bare question with real, already-fetched search results so
    the model has genuine material to cite instead of having to recall a
    URL from training data (which non-browsing models can't do reliably —
    see the note at the run_agent3 call site). Falls back to the bare
    question when no search evidence is available, so behaviour is
    unchanged for anyone not using a search API key."""
    usable = [r for r in (titled_results or []) if r.get("link")]
    if not usable:
        return question
    lines = []
    for i, r in enumerate(usable, start=1):
        title = r.get("title") or r.get("link")
        snippet = f" — {r['snippet']}" if r.get("snippet") else ""
        lines.append(f"{i}. {title}: {r['link']}{snippet}")
    return GROUNDING_TEMPLATE.format(question=question, sources_block="\n".join(lines))


def _default_cell(llm: str) -> dict:
    # PROVIDER_DEFAULT_STATUS only has real strings for Claude/Perplexity
    # ("Not tested") and Copilot ("Future scope") — ChatGPT/Gemini map to
    # None there since they're normally active by default. But the UI lets
    # a user run with ANY subset of active LLMs (e.g. ChatGPT only), so an
    # excluded ChatGPT or Gemini must still render as "Not tested", not as
    # a bare `None` that downstream code can't distinguish from "tested and
    # said no." The `or "Not tested"` covers exactly that gap.
    status = PROVIDER_DEFAULT_STATUS.get(llm) or "Not tested"
    return {
        "mentioned": {}, "position": {}, "cited_urls": status, "domains": status,
        "source_status": status, "response_text": "",
        "citation_claimed_count": 0, "citation_verified_count": 0,
    }


def run_agent3(
    run_id: str,
    store: "ss.StateStore",
    key_manager,
    test_set_rows: list,
    brand: str,
    competitors: list,
    providers: dict,          # {"ChatGPT": GroqChatGPTProvider(), "Gemini": GeminiProvider(), ...}
    active_llms: list,        # subset of ALL_LLMS this run actually calls
    search_client=None,
    search_api_key: str = "",
    location: str = "",
    groq_rate_limit_delay_s: float = 0.0,
    gemini_rate_limit_delay_s: float = 0.0,
    provider_limits: dict = None,           # {"ChatGPT": {"rpm":30,"tpm":8000,"rpd":1000,"tpd":200000}, "Gemini": {...}}
    rate_limiter_registry=None,             # RateLimiterRegistry; pass one in to persist budgets across resumes
    log=None,
    session=None,
):
    from ..rate_limiter import RateLimiterRegistry, estimate_tokens

    provider_limits = provider_limits or {}
    registry = rate_limiter_registry or RateLimiterRegistry()
    entities = [brand] + list(competitors)
    degraded_to_not_tested = set()  # providers downgraded mid-run via InsufficientCreditError

    # Starting (or resuming) a run always flips it back to active; it only
    # becomes paused_awaiting_key again below if a key pool is truly exhausted.
    store.set_run_status(run_id, ss.RUN_ACTIVE)

    # 1. Seed every unit as pending (idempotent — safe on resume).
    unit_keys = [unit_key(row["num"], llm) for row in test_set_rows for llm in ALL_LLMS]
    ss.ensure_units_pending(store, run_id, MODULE, unit_keys)

    # 2. Immediately settle non-active providers with their sample-matching default text
    #    (Claude/Perplexity -> "Not tested", Copilot -> "Future scope") — no network call.
    for row in test_set_rows:
        for llm in ALL_LLMS:
            if llm in active_llms:
                continue
            uk = unit_key(row["num"], llm)
            existing = store.get_unit(run_id, MODULE, uk)
            if existing and existing["status"] == ss.DONE:
                continue
            store.upsert_unit(run_id, MODULE, uk, ss.DONE, result=_default_cell(llm))

    paused = False
    for row in test_set_rows:
        if paused:
            break
        prompt_text = row["conversational_prompt"]

        # --- independent search-evidence pass (once per prompt, cached via a
        #     dedicated unit so it also survives resume) ---
        search_unit = unit_key(row["num"], "search_evidence")
        existing_search = store.get_unit(run_id, MODULE, search_unit)
        if existing_search and existing_search["status"] == ss.DONE:
            search_evidence = existing_search["result"]
        else:
            search_evidence = {"urls": [], "domains": [], "titled_results": []}
            if search_client and search_api_key:
                try:
                    res = search_client.search(prompt_text, search_api_key, location=location)
                    urls = res.get("organic_urls", [])[:5]
                    titled_results = res.get("organic_results", [])[:5]
                    domains = []
                    for u in urls:
                        try:
                            from urllib.parse import urlparse
                            d = urlparse(u).netloc.replace("www.", "")
                        except ValueError:
                            d = ""
                        if d and d not in domains:
                            domains.append(d)
                    search_evidence = {"urls": urls, "domains": domains, "titled_results": titled_results}
                except QuotaExceededError as e:
                    if log:
                        log(f"Search evidence quota exhausted: {e}")
                except ProviderError as e:
                    if log:
                        log(f"Search evidence lookup failed for prompt #{row['num']}: {e}")
            store.upsert_unit(run_id, MODULE, search_unit, ss.DONE, result=search_evidence)

        # Ground the prompt in real, live search results when we have them.
        # A non-browsing model (e.g. gpt-oss via Groq) can only "cite" a URL
        # by recalling one from training data — which for anything beyond
        # famous/stable URLs is usually a guess, and honestly-implemented
        # verification (url_verify) then correctly rejects it, leaving every
        # "Cited URLs" cell reading "No exact page URLs available." Handing
        # the model real, already-fetched URLs to choose from gives it
        # something genuine to cite instead of asking it to recall one from
        # memory — url_verify still independently re-checks reachability
        # regardless, so this doesn't weaken the anti-hallucination guarantee,
        # it just gives real citations an actual chance of showing up.
        llm_prompt_text = build_grounded_prompt(prompt_text, search_evidence.get("titled_results", []))

        for llm in active_llms:
            if llm in degraded_to_not_tested:
                store.upsert_unit(run_id, MODULE, unit_key(row["num"], llm), ss.DONE, result=_default_cell(llm))
                continue

            uk = unit_key(row["num"], llm)
            existing = store.get_unit(run_id, MODULE, uk)
            if existing and existing["status"] == ss.DONE:
                continue  # already done — this is exactly what makes resume skip real work

            provider_key_name = {"ChatGPT": "groq", "Gemini": "gemini", "Claude": "claude", "Perplexity": "perplexity"}[llm]
            provider = providers.get(llm)
            if provider is None or not key_manager.any_configured(provider_key_name):
                store.upsert_unit(run_id, MODULE, uk, ss.DONE, result=_default_cell(llm))
                continue

            api_key = key_manager.current_key(provider_key_name)
            if api_key is None:
                store.set_run_status(run_id, ss.RUN_PAUSED)
                store.upsert_unit(run_id, MODULE, uk, ss.FAILED_QUOTA, error="No live key remaining in pool")
                paused = True
                break

            limits = provider_limits.get(llm, {})
            limiter = registry.get(
                llm, api_key, rpm=limits.get("rpm"), tpm=limits.get("tpm"),
                rpd=limits.get("rpd"), tpd=limits.get("tpd"),
            )
            # A generous headroom buffer for expected completion tokens (the
            # grounded prompt's own length is known now; the model's answer
            # isn't yet) — real usage replaces this estimate right after the
            # call, so this only affects pre-call pacing, never the record.
            estimated_call_tokens = estimate_tokens(llm_prompt_text) + 400

            try:
                if limiter.daily_budget_exceeded(estimated_tokens=estimated_call_tokens):
                    # Proactively treat a spent-out daily RPD/TPD budget exactly like a
                    # server-side 429: rotate to a fresh key if one exists, otherwise
                    # pause cleanly. This reuses the existing, already-tested rotation/
                    # pause/resume machinery instead of a second parallel code path.
                    raise QuotaExceededError(
                        f"{llm}: local daily budget tracker says this key's RPD/TPD would be "
                        f"exceeded (requests_today={limiter._day_requests}, tokens_today={limiter._day_tokens})."
                    )
                limiter.wait_if_needed(estimated_tokens=estimated_call_tokens, log=log)
                resp: ProviderResponse = provider.generate(llm_prompt_text, api_key)
                actual_tokens = (resp.usage or {}).get("total_tokens") or estimated_call_tokens
                limiter.record(actual_tokens)
            except QuotaExceededError as e:
                nxt = key_manager.rotate(provider_key_name, error=str(e))
                if nxt is None:
                    store.set_run_status(run_id, ss.RUN_PAUSED)
                    store.upsert_unit(run_id, MODULE, uk, ss.FAILED_QUOTA, error=str(e))
                    if log:
                        log(f"{llm} key pool exhausted — run paused_awaiting_key.")
                    paused = True
                    break
                else:
                    store.upsert_unit(run_id, MODULE, uk, ss.FAILED_QUOTA, error=str(e))
                    if log:
                        log(f"{llm} quota hit on prompt #{row['num']} — rotated key, will retry on resume.")
                    continue
            except InsufficientCreditError as e:
                degraded_to_not_tested.add(llm)
                store.upsert_unit(run_id, MODULE, uk, ss.DONE, result=_default_cell(llm))
                if log:
                    log(f"{llm} trial credit exhausted — degrading to 'Not tested' for the rest of this run.")
                continue
            except ProviderError as e:
                store.upsert_unit(run_id, MODULE, uk, ss.FAILED_OTHER, error=str(e))
                if log:
                    log(f"{llm} error on prompt #{row['num']} (non-quota): {e}")
                continue

            mentions = parse_brand_mentions(resp.text, entities)
            checks = url_verify.verify_urls(resp.cited_urls, session=session) if resp.cited_urls else []
            cited_cell, source_status, domains_cell = url_verify.classify_citation(resp.cited_urls, checks=checks)

            cell = {
                "mentioned": {e: mentions[e]["mentioned"] for e in entities},
                "position": {e: mentions[e]["position"] for e in entities},
                "cited_urls": cited_cell,
                "domains": domains_cell,
                "source_status": source_status,
                "response_text": resp.text,
                "citation_claimed_count": len(resp.cited_urls or []),
                "citation_verified_count": sum(1 for c in checks if c.reachable),
            }
            store.upsert_unit(run_id, MODULE, uk, ss.DONE, result=cell)

            # Optional extra fixed floor on top of the rate limiter's own pacing —
            # useful if you're sharing a key with other usage outside this app
            # that the token/request limiter above has no visibility into.
            if llm == "Gemini" and gemini_rate_limit_delay_s > 0:
                time.sleep(gemini_rate_limit_delay_s)
            elif llm == "ChatGPT" and groq_rate_limit_delay_s > 0:
                time.sleep(groq_rate_limit_delay_s)

    if not paused:
        remaining = store.pending_or_failed_quota(run_id, MODULE)
        if not remaining:
            store.set_run_status(run_id, ss.RUN_ACTIVE)  # this module is clear; pipeline decides final RUN_COMPLETE
    return build_execution_rows(store, run_id, test_set_rows, entities)


def build_execution_rows(store, run_id, test_set_rows, entities) -> list:
    """Assemble 'LLM Execution' sheet rows from whatever units are currently `done`
    in SQLite — safe to call at any point, including mid-pause, for progress display."""
    rows = []
    for row in test_set_rows:
        out = {
            "num": row["num"],
            "conversational_prompt": row["conversational_prompt"],
            "intent_category": row["intent_category"],
            "per_llm": {},
        }
        for llm in ALL_LLMS:
            u = store.get_unit(run_id, MODULE, unit_key(row["num"], llm))
            if u and u["result"]:
                out["per_llm"][llm] = u["result"]
            else:
                out["per_llm"][llm] = {
                    "mentioned": {}, "position": {},
                    "cited_urls": "Pending" if not u else u.get("error", "Pending"),
                    "domains": "Pending", "source_status": "Pending", "response_text": "",
                    "citation_claimed_count": 0, "citation_verified_count": 0,
                }
        se = store.get_unit(run_id, MODULE, unit_key(row["num"], "search_evidence"))
        se_result = se["result"] if se else {"urls": [], "domains": []}
        out["search_evidence_urls"] = se_result.get("urls", [])
        out["search_evidence_domains"] = se_result.get("domains", [])
        rows.append(out)
    return rows
