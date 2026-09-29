"""
Executive Summary (Module D closer).

Every number this needs already exists after Agents 5, 6, 7, 9 and the kpi.py
helpers run — so the template version costs nothing. An LLM-polished
narrative version is available too, but costs exactly one extra call for the
*whole run* (negligible against a 1,000/day Groq budget), and always falls
back to the template on any provider error so a missing/exhausted key never
blocks delivery of the summary.
"""
from __future__ import annotations

from ..providers.base import ProviderError, QuotaExceededError


def build_summary_data(
    config: dict,
    gevs_result: dict,
    citation_intel_rows: list,
    gap_result: dict,
    content_gaps_targets: dict,
    draft_result: dict,
    citation_accuracy: dict,
    prompt_relevance: dict,
    processing_speed: dict,
) -> dict:
    main_table = gevs_result["main_table"]
    brand_stats = next(s for s in main_table if s["entity"] == gevs_result["brand"])
    leader = main_table[0]
    top_domain = citation_intel_rows[0]["domain"] if citation_intel_rows else "—"
    top_domain_citations = citation_intel_rows[0]["total_citations"] if citation_intel_rows else 0

    return {
        "brand": config["brand"], "sector": config.get("sector", ""), "region": config.get("region", ""),
        "brand_gevs_top3": brand_stats["gevs_top3"], "brand_gevs_overall": brand_stats["gevs_overall"],
        "brand_rank": brand_stats["rank"], "brand_total_mentions": brand_stats["total_mentions"],
        "leader_brand": leader["entity"], "leader_gevs_top3": leader["gevs_top3"],
        "gap_to_leader_pp": round(leader["gevs_top3"] - brand_stats["gevs_top3"], 1),
        "is_leader": leader["entity"] == gevs_result["brand"],
        "top_domain": top_domain, "top_domain_citations": top_domain_citations,
        "content_gaps_identified": content_gaps_targets["content_gaps_identified"],
        "quick_wins": content_gaps_targets["quick_wins"],
        "medium_term": content_gaps_targets["medium_term"],
        "strategic": content_gaps_targets["strategic"],
        "drafts_generated": len(draft_result.get("strategy_rows", [])),
        "citation_accuracy_pct": citation_accuracy["accuracy_pct"],
        "citation_accuracy_target_pct": citation_accuracy["target_pct"],
        "citation_accuracy_meets_target": citation_accuracy["meets_target"],
        "prompt_relevance_pct": prompt_relevance["relevance_pct"],
        "prompt_relevance_rated_count": prompt_relevance["rated_count"],
        "prompt_relevance_total": prompt_relevance["total_prompts"],
        "processing_elapsed_s": processing_speed["total_elapsed_s"],
        "processing_target_s": processing_speed["target_s"],
        "processing_meets_target": processing_speed["meets_target"],
    }


def render_template_summary(data: dict) -> str:
    from .. import kpi

    leader_line = (
        f"**{data['brand']}** is the current category leader on GEVS Top-3 visibility."
        if data["is_leader"] else
        f"**{data['brand']}** trails category leader **{data['leader_brand']}** "
        f"({data['leader_gevs_top3']}% vs {data['brand_gevs_top3']}%) by {data['gap_to_leader_pp']} "
        f"percentage points on GEVS Top-3 visibility."
    )
    citation_acc = (
        f"{data['citation_accuracy_pct']}% (target: {data['citation_accuracy_target_pct']}%, "
        f"{'met' if data['citation_accuracy_meets_target'] else 'not yet met'})"
        if data["citation_accuracy_pct"] is not None else "not enough claimed citations this run to measure"
    )
    relevance = (
        f"{data['prompt_relevance_pct']}% of {data['prompt_relevance_rated_count']} rated prompts "
        f"(of {data['prompt_relevance_total']} total)"
        if data["prompt_relevance_pct"] is not None else "not rated yet — no prompts rated this run"
    )
    speed = (
        f"{kpi.format_seconds(data['processing_elapsed_s'])} "
        f"(target: {kpi.format_seconds(data['processing_target_s'])}, "
        f"{'met' if data['processing_meets_target'] else 'not met — see README on free-tier rate limits'})"
    )

    return f"""# Executive Summary — {data['brand']}

{leader_line}

## Visibility
- GEVS Top-3 Visibility: **{data['brand_gevs_top3']}%** (Rank #{data['brand_rank']})
- GEVS Overall: {data['brand_gevs_overall']}% ({data['brand_total_mentions']} total mentions)
- Most-cited source across all tested LLMs: **{data['top_domain']}** ({data['top_domain_citations']} citations)

## Content Strategy
- Content gaps identified: {data['content_gaps_identified']}
  ({data['quick_wins']} quick wins ≤30 days, {data['medium_term']} medium-term 31-90 days, {data['strategic']} strategic 90+ days)
- Draft articles generated this run: {data['drafts_generated']}

## Success Metrics (PRD KPIs)
- Citation Accuracy: {citation_acc}
- Prompt Relevance (user-rated): {relevance}
- Processing Speed: {speed}
"""


LLM_POLISH_PROMPT = """Rewrite the following structured run summary as a tight, 150-200 word
executive summary for a marketing stakeholder who has never seen this dashboard
before. Keep every number exactly as given — do not invent, round differently,
or omit any of them. Plain prose, no headers, no bullet points, no preamble.

{template_summary}
"""


def render_llm_polished_summary(data: dict, llm_client=None, llm_api_key: str = None, log=None) -> str:
    """One extra LLM call for the whole run. Falls back to the template on
    any provider error, missing key, or missing client — the summary is
    always produced either way."""
    template = render_template_summary(data)
    if not llm_client or not llm_api_key:
        return template
    try:
        resp = llm_client.generate(LLM_POLISH_PROMPT.format(template_summary=template), llm_api_key)
        polished = resp.text.strip()
        return f"# Executive Summary — {data['brand']}\n\n{polished}" if polished else template
    except QuotaExceededError as e:
        if log:
            log(f"Executive summary LLM polish skipped (quota): {e}")
        return template
    except ProviderError as e:
        if log:
            log(f"Executive summary LLM polish skipped (error): {e}")
        return template
