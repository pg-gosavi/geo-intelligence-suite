"""
Agent 2 — Consolidation Engine (Module A).

Filters duplicates, merges similar intent, and finalises a Test Set of at
most `max_test_set` (default 30, per the PRD) high-value prompts from the
raw candidate list Agent 1 produced.
"""
from __future__ import annotations

import difflib
import re

PRIORITY_ORDER = {"H": 0, "M": 1, "L": 2}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def is_duplicate(a: str, b: str, threshold: float = 0.82) -> bool:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= threshold


def dedupe(rows: list) -> list:
    """Keep the first occurrence of each near-duplicate conversational prompt;
    merge notes so the dropped duplicate's source is not silently lost."""
    kept = []
    for row in rows:
        merged = False
        for existing in kept:
            if is_duplicate(row["conversational_prompt"], existing["conversational_prompt"]):
                sources = {existing["source"], row["source"]}
                existing["notes"] = (existing.get("notes") or "") + f" | merged duplicate from source={row['source']}"
                if PRIORITY_ORDER.get(row["priority"], 1) < PRIORITY_ORDER.get(existing["priority"], 1):
                    existing["priority"] = row["priority"]
                merged = True
                break
        if not merged:
            kept.append(row)
    return kept


def run_agent2(rows: list, max_test_set: int = 30) -> list:
    """Mutates and returns `rows`: dedupes, then marks up to `max_test_set`
    prompts (favouring seed prompts and higher priority) as the Test Set."""
    deduped = dedupe(rows)

    def sort_key(r):
        source_rank = 0 if r["source"] == "seed" else (1 if r["source"] in ("PAA", "llm_db") else 2)
        return (source_rank, PRIORITY_ORDER.get(r["priority"], 1))

    ranked = sorted(deduped, key=sort_key)
    for i, row in enumerate(ranked):
        row["agent2_status"] = "Done"
        if i < max_test_set:
            row["include_in_test_set"] = "Yes"
            row["notes"] = row.get("notes") or f"Selected for test set (rank {i + 1})"
            row["approved_by"] = "Client review"
        else:
            row["include_in_test_set"] = "No"
            row["notes"] = "Not selected for this execution"
            row["agent2_status"] = "Not selected"
            row["approved_by"] = ""
    return ranked


def numbered(rows: list) -> list:
    """Assign the '#' column, 1-indexed, in list order."""
    for i, row in enumerate(rows, start=1):
        row["num"] = i
    return rows
