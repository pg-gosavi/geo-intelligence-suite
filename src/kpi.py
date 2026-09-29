"""
kpi.py — the three PRD success metrics that weren't previously tracked as
named numbers anywhere in the app: Prompt Relevance, Processing Speed, and
Citation Accuracy. None of these need an extra API call — see the
docstring on each function for exactly what it aggregates and from where.

Prompt Relevance and run timing are persisted as ordinary `units` rows in
the existing SQLite run-state store (module="prompt_relevance" /
module="run_timing"), reusing state_store.py's existing atomic-write
machinery rather than adding a second storage mechanism.
"""
from __future__ import annotations

import time

from . import state_store as ss

RELEVANCE_MODULE = "prompt_relevance"
TIMING_MODULE = "run_timing"

# The PRD's own targets, stated plainly so a run can be judged against them
# rather than just displaying a bare number with no reference point.
CITATION_ACCURACY_TARGET_PCT = 80.0
PROCESSING_SPEED_TARGET_S = 180.0  # "<3 minutes for 30 prompts" — see README/TESTING_REPORT
# on why this target is not achievable on free-tier rate limits regardless
# of app-side optimisation; tracked here for transparency, not pretended away.


# ---------------------------------------------------------------------------
# Citation Accuracy — % of citation URLs an LLM claimed that verified as
# actually reachable. Zero extra calls: agent3_llm_execution.py already
# records citation_claimed_count/citation_verified_count on every cell via
# url_verify's existing HEAD/GET check; this just aggregates numbers that
# already exist.
# ---------------------------------------------------------------------------
def compute_citation_accuracy(execution_rows: list, active_llms: list) -> dict:
    claimed_total = 0
    verified_total = 0
    per_llm: dict[str, dict] = {}

    for row in execution_rows:
        for llm in active_llms:
            cell = row.get("per_llm", {}).get(llm, {})
            claimed = cell.get("citation_claimed_count", 0) or 0
            verified = cell.get("citation_verified_count", 0) or 0
            claimed_total += claimed
            verified_total += verified
            bucket = per_llm.setdefault(llm, {"claimed": 0, "verified": 0})
            bucket["claimed"] += claimed
            bucket["verified"] += verified

    accuracy_pct = round(verified_total / claimed_total * 100, 1) if claimed_total else None
    per_llm_pct = {
        llm: (round(v["verified"] / v["claimed"] * 100, 1) if v["claimed"] else None)
        for llm, v in per_llm.items()
    }
    return {
        "claimed_total": claimed_total,
        "verified_total": verified_total,
        "accuracy_pct": accuracy_pct,
        "target_pct": CITATION_ACCURACY_TARGET_PCT,
        "meets_target": (accuracy_pct is not None and accuracy_pct >= CITATION_ACCURACY_TARGET_PCT),
        "per_llm": per_llm,
        "per_llm_pct": per_llm_pct,
    }


# ---------------------------------------------------------------------------
# Prompt Relevance — "% of generated prompts deemed relevant by the user."
# The PRD's own wording is a human judgment, not an AI one, so this is a
# thumbs up/down UI per Test Set prompt (see app.py), persisted per run.
# ---------------------------------------------------------------------------
def record_prompt_relevance(store: "ss.StateStore", run_id: str, prompt_num: int, relevant: bool) -> None:
    store.upsert_unit(run_id, RELEVANCE_MODULE, str(prompt_num), ss.DONE, result={"relevant": bool(relevant)})


def get_prompt_relevance_ratings(store: "ss.StateStore", run_id: str) -> dict:
    """{prompt_num: bool} for every prompt rated so far this run."""
    units = store.get_units(run_id, module=RELEVANCE_MODULE, status=ss.DONE)
    out = {}
    for u in units:
        try:
            out[int(u["unit_key"])] = bool(u["result"]["relevant"])
        except (TypeError, KeyError, ValueError):
            continue
    return out


def compute_prompt_relevance(store: "ss.StateStore", run_id: str, test_set_rows: list) -> dict:
    ratings = get_prompt_relevance_ratings(store, run_id)
    total_prompts = len(test_set_rows)
    rated_count = len(ratings)
    relevant_count = sum(1 for v in ratings.values() if v)
    relevance_pct = round(relevant_count / rated_count * 100, 1) if rated_count else None
    return {
        "total_prompts": total_prompts,
        "rated_count": rated_count,
        "relevant_count": relevant_count,
        "relevance_pct": relevance_pct,
        "fully_rated": rated_count >= total_prompts and total_prompts > 0,
    }


# ---------------------------------------------------------------------------
# Processing Speed — elapsed wall-clock time for the run, tracked honestly
# against the PRD's <3-minute target rather than silently omitted because
# that target isn't reachable on free-tier rate limits (see README).
# ---------------------------------------------------------------------------
def start_stage_timer() -> float:
    return time.time()


def record_stage_timing(store: "ss.StateStore", run_id: str, stage: str, started_at: float, ended_at: float = None) -> dict:
    ended_at = ended_at if ended_at is not None else time.time()
    elapsed_s = round(ended_at - started_at, 1)
    result = {"started_at": started_at, "ended_at": ended_at, "elapsed_s": elapsed_s}
    store.upsert_unit(run_id, TIMING_MODULE, stage, ss.DONE, result=result)
    return result


def get_processing_speed(store: "ss.StateStore", run_id: str) -> dict:
    units = store.get_units(run_id, module=TIMING_MODULE, status=ss.DONE)
    stages = {u["unit_key"]: u["result"] for u in units if u["result"]}
    total_elapsed_s = round(sum(s.get("elapsed_s", 0) for s in stages.values()), 1)
    return {
        "stages": stages,
        "total_elapsed_s": total_elapsed_s,
        "target_s": PROCESSING_SPEED_TARGET_S,
        "meets_target": total_elapsed_s > 0 and total_elapsed_s <= PROCESSING_SPEED_TARGET_S,
    }


def format_seconds(total_s: float) -> str:
    total_s = max(0, int(total_s))
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes} min {seconds} sec"
