"""
Agent 9 — Summary & Export (Module D).

Builds the "Rank Improvement Recs" sheet (per prompt x LLM, what it would
take to move the brand up) plus a run-level executive-summary row, then
hands everything to excel_export.py / html_export.py for the two final
deliverables.
"""
from __future__ import annotations

FEES_INTENTS = {"fees_pricing"}


def _rank_int(position: str):
    if not position or position == "-":
        return None
    try:
        return int(position.lstrip("#"))
    except ValueError:
        return None


def _guide_type(intent: str) -> str:
    return "fees & cost explainer" if intent in FEES_INTENTS else "long-term strategy guide"


def build_rank_row(prompt_num, prompt_text, intent, llm, brand, competitors, cell) -> dict:
    positions = cell.get("position", {})
    brand_pos = positions.get(brand, "-")
    brand_rank = _rank_int(brand_pos)

    competitor_ranks = {c: _rank_int(positions.get(c, "-")) for c in competitors}
    competitor_ranks = {c: r for c, r in competitor_ranks.items() if r is not None}

    all_ranked = {**({brand: brand_rank} if brand_rank else {}), **competitor_ranks}

    if brand_rank == 1:
        return {
            "prompt_num": prompt_num, "prompt": prompt_text, "intent": intent, "llm": llm,
            "current_brand_rank": "#1", "leader_brand": brand, "leader_rank": "#1",
            "rank_gap": "No rank improvement required",
            "suggested_lever": "No rank improvement required. Maintain current answer coverage, refresh citations, and monitor competitor movement.",
            "root_cause": "Brand already ranks #1 in this tested model response for the prompt.",
            "recommended_action": "No new rank-improvement action. Keep page evidence fresh and track this prompt in the next monthly/quarterly run.",
            "target_6m": "No action - already #1", "target_12m": "No action - already #1",
            "rank_status": "Brand leading",
        }

    if not competitor_ranks and brand_rank is None:
        return {
            "prompt_num": prompt_num, "prompt": prompt_text, "intent": intent, "llm": llm,
            "current_brand_rank": "Tested; not cited and no competitor rank measured",
            "leader_brand": f"No competitor cited by {llm}", "leader_rank": "No leader cited",
            "rank_gap": f"No measurable competitor rank by {llm}",
            "suggested_lever": "No measurable LLM citations for this prompt — review the prompt's commercial intent or LLM evidence quality before drafting content.",
            "root_cause": f"Sparse {llm} citation for this query — evidence base weak across all brands; may indicate weak commercial intent.",
            "recommended_action": "Diagnose first: review prompt's commercial intent and widen evidence base before drafting. May indicate weak market signal, not a content gap.",
            "target_6m": "#5 (enter visibility band)", "target_12m": "#3 (push into top-3)",
            "rank_status": "Tested; no measurable brand/competitor rank",
        }

    if brand_rank is None and competitor_ranks:
        leader = min(competitor_ranks, key=competitor_ranks.get)
        lo, hi = min(competitor_ranks.values()), max(competitor_ranks.values())
        rng = f"#{lo}" if lo == hi else f"#{lo} to #{hi}"
        guide = _guide_type(intent)
        return {
            "prompt_num": prompt_num, "prompt": prompt_text, "intent": intent, "llm": llm,
            "current_brand_rank": f"Not cited (competitors at {rng})",
            "leader_brand": leader, "leader_rank": f"#{competitor_ranks[leader]}",
            "rank_gap": f">{hi} (brand absent; competitors at {rng})",
            "suggested_lever": (
                f"Brand is absent but {leader} is cited at #{competitor_ranks[leader]}. Create an authoritative "
                f"{guide} that matches {leader}'s citation depth and benchmark against {rng}. "
                + ("Surface pricing/fees in a parseable table with last-reviewed date."
                   if guide.startswith("fees") else
                   "Add a comparison table with ≥3 named alternatives and an objective verdict.")
            ),
            "root_cause": f"Brand absent from {llm} for this query; {leader} cited at #{competitor_ranks[leader]} — LLM lacks brand-named evidence for this intent.",
            "recommended_action": f"Acquire evidence presence: pitch contributor article on {llm}-favored publishers; mirror {leader}'s coverage depth on {brand}'s own page.",
            "target_6m": "#5 (enter visibility band)", "target_12m": "#3 (push into top-3)",
            "rank_status": "Brand absent; competitor cited",
        }

    # Brand cited but not #1, at least one competitor also ranked.
    leader = min(all_ranked, key=all_ranked.get)
    gap = brand_rank - all_ranked[leader]
    return {
        "prompt_num": prompt_num, "prompt": prompt_text, "intent": intent, "llm": llm,
        "current_brand_rank": brand_pos, "leader_brand": leader, "leader_rank": f"#{all_ranked[leader]}",
        "rank_gap": gap,
        "suggested_lever": f"Close the {gap}-position gap to {leader}: match its citation depth and add brand-specific proof points.",
        "root_cause": f"Brand trails {leader} on {llm} for this query.",
        "recommended_action": f"Strengthen the page currently ranked {brand_pos} so it out-cites {leader}'s evidence.",
        "target_6m": "#3 (push into top-3)", "target_12m": "#1 (contest leadership)",
        "rank_status": "Brand trailing leader",
    }


def build_rank_improvement_recs(execution_rows: list, brand: str, competitors: list, active_llms: list) -> dict:
    rows = []
    total_cells = 0
    cited_cells = 0
    top3_cells = 0
    leader_counts: dict = {}

    for row in execution_rows:
        for llm in active_llms:
            cell = row["per_llm"].get(llm, {})
            r = build_rank_row(row["num"], row["conversational_prompt"], row["intent_category"],
                                llm, brand, competitors, cell)
            rows.append(r)
            total_cells += 1
            brand_rank = _rank_int(cell.get("position", {}).get(brand, "-"))
            if brand_rank is not None:
                cited_cells += 1
                if brand_rank <= 3:
                    top3_cells += 1
            if r["rank_status"] == "Brand absent; competitor cited":
                leader_counts[r["leader_brand"]] = leader_counts.get(r["leader_brand"], 0) + 1

    coverage_pct = round(cited_cells / total_cells * 100, 1) if total_cells else 0.0
    top_leader = max(leader_counts, key=leader_counts.get) if leader_counts else None
    n_prompts_with_agent7 = len({r["prompt_num"] for r in rows})

    summary = {
        "prompt_num": "Summary", "prompt": "(All prompts × LLMs — run-level metrics)",
        "intent": "rank_summary", "llm": "All tested LLMs (aggregate)",
        "current_brand_rank": f"#1 (avg of {cited_cells}/{total_cells} LLM-cells cited; {coverage_pct}% coverage)"
                                if cited_cells else f"Not cited in {total_cells} LLM-cells tested",
        "leader_brand": (f"{top_leader} (leader in {leader_counts[top_leader]}/{total_cells} LLM-cells)"
                          if top_leader else "No competitor consistently leads"),
        "leader_rank": "-", "rank_gap": "-",
        "suggested_lever": f"Brand cited in {cited_cells} of {total_cells} LLM-cells; top-3 in {top3_cells}.",
        "root_cause": f"{n_prompts_with_agent7} prompts with Agent 7 findings; review per-prompt rows below.",
        "recommended_action": "Focus on top page-issue labels surfacing across the run; prioritise [Own] fixes first.",
        "target_6m": "#1 (maintain leadership) (suggested aggregate target)" if coverage_pct >= 50
                     else "#5 (enter visibility band) (suggested aggregate target)",
        "target_12m": "#1 (defend leadership) (suggested aggregate target)" if coverage_pct >= 50
                      else "#3 (push into top-3) (suggested aggregate target)",
        "rank_status": "",
    }
    return {"summary": summary, "rows": rows}
