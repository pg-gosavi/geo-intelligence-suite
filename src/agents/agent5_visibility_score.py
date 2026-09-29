"""
Agent 5 — Visibility Score / Share of Voice (Module C).

Formulas (reverse-engineered from the sample workbook and verified in
tests/test_scoring.py against its own "Prompt Discovery" + "LLM Execution"
sheets as ground truth):

    Mention Rate % == GEVS Overall %
        = (mentions at ANY position) / (prompts_tested * models_tested) * 100

    GEVS Top-3 Visibility %
        = (mentions at position #1-#3 only) / (prompts_tested * models_tested) * 100

    vs Brand GEVS Gap
        = (this brand's GEVS Top-3 %) - (the benchmark brand's GEVS Top-3 %)
        ("—" for the benchmark brand's own row)

    Avg Position (when cited)
        = mean of the numeric rank across all cited instances for that brand

`models_tested` counts only the LLMs actually executed this run (the
providers in `active_llms`), matching the sample: it tested 2 active models
(ChatGPT, Gemini) against 15 prompts, and UTI Mutual Fund's 3 total mentions
divide by 15*2=30 for its 10% Mention Rate.
"""
from __future__ import annotations

import statistics

from ..schema import ALL_LLMS


def _position_rank(position: str):
    if not position or position == "-":
        return None
    try:
        return int(position.lstrip("#"))
    except ValueError:
        return None


def compute_entity_stats(execution_rows: list, entity: str, active_llms: list) -> dict:
    # Only the LLMs actually executed this run get a numeric count here.
    # An LLM that wasn't in `active_llms` (never called at all) must stay
    # absent from this dict — not default to 0 — so exporters can tell
    # "tested, zero mentions" apart from "never tested." Populating every
    # key in ALL_LLMS regardless of active_llms was a real bug: it made an
    # LLM that was never even called show a literal 0 in the report instead
    # of "Not tested" / "Future scope", which reads as "this model reviewed
    # every prompt and just never mentioned the brand" — a materially
    # different (and false) claim.
    per_llm_mentions = {llm: 0 for llm in active_llms}
    total_mentions = 0
    top3_mentions = 0
    positions_when_cited = []

    for row in execution_rows:
        for llm in active_llms:
            cell = row["per_llm"].get(llm, {})
            mentioned_map = cell.get("mentioned", {})
            position_map = cell.get("position", {})
            if mentioned_map.get(entity):
                per_llm_mentions[llm] += 1
                total_mentions += 1
                rank = _position_rank(position_map.get(entity))
                if rank is not None:
                    positions_when_cited.append(rank)
                    if rank <= 3:
                        top3_mentions += 1

    prompts_tested = len(execution_rows)
    models_tested = max(len(active_llms), 1)
    denom = prompts_tested * models_tested

    mention_rate = round(total_mentions / denom * 100, 1) if denom else 0.0
    gevs_top3 = round(top3_mentions / denom * 100, 1) if denom else 0.0
    avg_position = round(statistics.mean(positions_when_cited), 1) if positions_when_cited else "-"

    return {
        "entity": entity,
        "per_llm_mentions": per_llm_mentions,
        "total_mentions": total_mentions,
        "mention_rate": mention_rate,
        "gevs_overall": mention_rate,
        "gevs_top3": gevs_top3,
        "avg_position": avg_position,
    }


def status_label(entity: str, brand: str, rank: int, gap) -> str:
    if entity == brand:
        return "🟡 Benchmark brand — needs improvement" if rank != 1 else "🟢 Leader (benchmark brand)"
    if rank == 1:
        return "🟢 Leader"
    if gap == "—" or gap is None:
        return "🟡 Ahead of benchmark"
    return "🟡 Ahead of benchmark" if gap >= 0 else "🟡 Behind benchmark"


def run_agent5(execution_rows: list, brand: str, competitors: list, active_llms: list) -> dict:
    entities = [brand] + list(competitors)
    stats = {e: compute_entity_stats(execution_rows, e, active_llms) for e in entities}

    brand_top3 = stats[brand]["gevs_top3"]
    for e, s in stats.items():
        s["vs_gap"] = "—" if e == brand else round(s["gevs_top3"] - brand_top3, 1)

    def sort_key(e):
        s = stats[e]
        avg_pos = s["avg_position"] if isinstance(s["avg_position"], (int, float)) else 999
        return (-s["gevs_top3"], avg_pos)

    ranked_entities = sorted(entities, key=sort_key)
    for i, e in enumerate(ranked_entities, start=1):
        stats[e]["rank"] = i
        stats[e]["status"] = status_label(e, brand, i, stats[e]["vs_gap"])

    main_table = [stats[e] for e in ranked_entities]

    # Per-LLM breakdown for the benchmark brand only.
    prompts_tested = len(execution_rows)
    per_llm_mentions = stats[brand]["per_llm_mentions"]  # only has keys for active_llms now
    per_llm_gevs = {
        llm: (round(per_llm_mentions[llm] / prompts_tested * 100, 1) if prompts_tested and llm in active_llms else None)
        for llm in ALL_LLMS
    }
    tested_pct = {llm: pct for llm, pct in per_llm_gevs.items() if pct is not None}
    interpretation = ""
    if tested_pct:
        best_llm = max(tested_pct, key=tested_pct.get)
        worst_llm = min(tested_pct, key=tested_pct.get)
        leader_entity = ranked_entities[0]
        leader_top3 = stats[leader_entity]["gevs_top3"]
        overall = stats[brand]["gevs_overall"]
        if len(tested_pct) == 1:
            # A single active LLM makes "best (X%); worst (X%)" a redundant,
            # confusing sentence (X compared to itself) — say it plainly instead.
            spread_clause = f"{best_llm} is the only actively tested model ({tested_pct[best_llm]}%)."
        else:
            spread_clause = (
                f"{best_llm} gives the highest visibility ({tested_pct[best_llm]}%); "
                f"{worst_llm} the lowest ({tested_pct[worst_llm]}%)."
            )
        if leader_entity == brand:
            interpretation = (
                f"{brand} GEVS overall {overall}%. {spread_clause} "
                f"{brand} currently leads this benchmark set — focus on defending citation depth."
            )
        else:
            gap = round(leader_top3 - stats[brand]["gevs_top3"], 1)
            interpretation = (
                f"{brand} GEVS overall {overall}%. {spread_clause} "
                f"Focus E-E-A-T and citation-worthy content where visibility is weakest. "
                f"Brand trails category leader {leader_entity} ({leader_top3}%) by {gap} pp — priority gap to close."
            )

    per_llm_table = {
        "mentions": per_llm_mentions,
        "gevs_pct": per_llm_gevs,
        "interpretation": interpretation,
    }

    return {"main_table": main_table, "per_llm_table": per_llm_table, "brand": brand, "active_llms": list(active_llms)}
