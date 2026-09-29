"""
pipeline.py — orchestrates Agents 1-9 end to end for one run, on top of the
key-rotation + SQLite resume infrastructure. Used by both app.py (Streamlit)
and tests/test_end_to_end.py (with fake providers, zero real API calls).
"""
from __future__ import annotations

import io
import zipfile

from . import excel_export, html_export
from . import kpi
from . import state_store as ss
from .agents import (
    agent1_prompt_expansion as a1,
    agent2_consolidation as a2,
    agent3_llm_execution as a3,
    agent4_youtube as a4,
    agent5_visibility_score as a5,
    agent6_citation_ranking as a6,
    agent7_source_analysis as a7,
    agent8_content_generation as a8,
    agent9_summary_export as a9,
    executive_summary as a_exec,
)
from .schema import ACTIVE_LLMS_DEFAULT

# Free-tier request/token budgets used to proactively pace calls instead of
# reacting to 429s after the fact. These are starting points, not guarantees
# from the providers — override via config["provider_limits"] (the app's
# Advanced settings expose this) whenever your own dashboard shows different
# numbers. Groq's numbers below are the exact limits reported for
# openai/gpt-oss-120b on its free tier; Gemini's vary by exact model/region
# and are set conservatively — check https://ai.google.dev/gemini-api/docs/rate-limits
# for your key's actual current numbers.
DEFAULT_PROVIDER_LIMITS = {
    "ChatGPT": {"rpm": 30, "tpm": 8_000, "rpd": 1_000, "tpd": 200_000},   # Groq openai/gpt-oss-120b free tier
    "Gemini": {"rpm": 15, "tpm": 250_000, "rpd": 1_000},                  # conservative Flash-Lite free-tier estimate
}


def default_log(msg: str):
    print(f"[pipeline] {msg}")


def run_prompt_discovery(store, run_id, config, providers, key_manager, log=default_log):
    """Agents 1 + 2. Returns the finalised Prompt Discovery rows (Test Set marked)."""
    search_client = providers.get("search")
    search_key = key_manager.current_key("serpapi") or key_manager.current_key("serper")
    llm_client = providers.get("ChatGPT")
    llm_key = key_manager.current_key("groq")

    raw_rows = a1.run_agent1(
        config["seed_prompts"], config["brand"], config["competitors"], config["region"], config["sector"],
        search_client=search_client, search_api_key=search_key,
        llm_client=llm_client, llm_api_key=llm_key, log=log,
    )
    final_rows = a2.run_agent2(raw_rows, max_test_set=config.get("max_test_set", 30))
    a2.numbered(final_rows)
    return final_rows


def run_execution(store, run_id, config, providers, key_manager, test_set_rows, log=default_log, session=None,
                    rate_limiter_registry=None):
    """Agent 3. Mutates SQLite state; safe to call again to resume."""
    active_llms = [l for l in config.get("active_llms", ACTIVE_LLMS_DEFAULT)]
    search_client = providers.get("search")
    search_key = key_manager.current_key("serpapi") or key_manager.current_key("serper")
    provider_limits = config.get("provider_limits", DEFAULT_PROVIDER_LIMITS)

    execution_rows = a3.run_agent3(
        run_id, store, key_manager, test_set_rows,
        config["brand"], config["competitors"], providers, active_llms,
        search_client=search_client, search_api_key=search_key, location=config["region"],
        groq_rate_limit_delay_s=config.get("groq_rate_limit_delay_s", 0.0),
        gemini_rate_limit_delay_s=config.get("gemini_rate_limit_delay_s", 0.0),
        provider_limits=provider_limits, rate_limiter_registry=rate_limiter_registry,
        log=log, session=session,
    )

    # Reflect real attempt status back onto the Prompt Discovery rows —
    # test_set_rows holds the SAME dict objects as the full pd_rows list, so
    # this mutation is visible wherever pd_rows is used afterwards (e.g. the
    # xlsx export). A row is "Executed" once at least one active-LLM unit
    # for it has actually been attempted (done or failed) rather than left
    # pending — a run that paused partway through correctly leaves later
    # prompts as "Not executed" rather than claiming the whole Test Set ran.
    for row in test_set_rows:
        attempted = any(
            (u := store.get_unit(run_id, a3.MODULE, a3.unit_key(row["num"], llm))) and u["status"] != ss.PENDING
            for llm in active_llms
        )
        if attempted:
            row["execution_status"] = "Executed"

    run = store.get_run(run_id)
    return execution_rows, (run["status"] if run else ss.RUN_ACTIVE)


def run_analytics(config, execution_rows, log=default_log):
    """Agents 5 + 6."""
    active_llms = config.get("active_llms", ACTIVE_LLMS_DEFAULT)
    entity_websites = {config["brand"]: config.get("website", "")}
    gevs_result = a5.run_agent5(execution_rows, config["brand"], config["competitors"], active_llms)
    citation_ranking_rows = a6.build_citation_ranking(
        execution_rows, config["brand"], config["competitors"], active_llms,
        entity_websites=entity_websites,
    )
    citation_intel_rows = a6.build_citation_intel(execution_rows, config["brand"], active_llms,
                                                     entity_websites=entity_websites)
    return gevs_result, citation_ranking_rows, citation_intel_rows


def _pick_winning_links(execution_rows, citation_ranking_rows, config):
    """Winning links = the top verified, non-homepage cited URLs across the run.
    Used as the automatic fallback when the user doesn't make an explicit
    selection (see get_candidate_winning_links / run_content_remediation)."""
    ordered_urls, triggering = _collect_cited_urls(execution_rows)
    max_links = config.get("max_winning_links", 3)
    return ordered_urls[:max_links], triggering


def _collect_cited_urls(execution_rows):
    """Every verified, non-homepage URL any tested LLM cited, in first-seen
    order, plus the prompt(s) that triggered each citation."""
    triggering = {}
    ordered_urls = []
    for row in execution_rows:
        for llm, cell in row["per_llm"].items():
            urls = cell.get("cited_urls")
            if isinstance(urls, str) and urls.startswith("http"):
                for u in urls.split(" | "):
                    u = u.strip()
                    if u and u.startswith("http"):
                        if u not in ordered_urls:
                            ordered_urls.append(u)
                        triggering.setdefault(u, []).append(row["conversational_prompt"])
    return ordered_urls, triggering


def get_candidate_winning_links(execution_rows, citation_ranking_rows, citation_intel_rows, config,
                                  max_candidates: int = 10) -> list:
    """Human-in-the-loop data source (Module D step 1 of the PRD): every
    citable URL Agent 6 surfaced, ranked and annotated so a person can pick
    which ones actually get scraped/drafted-against in Agents 7-8, instead of
    the pipeline silently auto-picking the top N. Costs zero extra API calls
    — everything here was already computed by Agent 6."""
    from urllib.parse import urlparse

    ordered_urls, triggering = _collect_cited_urls(execution_rows)
    domain_stats = {r["domain"]: r for r in citation_intel_rows}

    candidates = []
    for url in ordered_urls:
        try:
            domain = urlparse(url).netloc.replace("www.", "")
        except ValueError:
            domain = url
        stats = domain_stats.get(domain, {})
        candidates.append({
            "url": url,
            "domain": domain,
            "total_citations_for_domain": stats.get("total_citations", 1),
            "source_quality": stats.get("source_quality", "unclassified_signal"),
            "sample_prompts": triggering.get(url, [])[:3],
            "times_cited_as_this_exact_url": len(triggering.get(url, [])),
        })

    candidates.sort(key=lambda c: (-c["total_citations_for_domain"], -c["times_cited_as_this_exact_url"]))
    return candidates[:max_candidates]


def run_content_remediation(config, execution_rows, citation_ranking_rows, providers, key_manager,
                              selected_links: list = None, log=default_log):
    """Agents 7 + 8. `selected_links`, when given, is the human-picked subset
    from get_candidate_winning_links (Module D's human-in-the-loop step) and
    is used exactly as provided; falls back to the automatic top-N pick only
    when the caller doesn't supply a selection at all (selected_links is
    None) — an explicit empty list means "the user picked nothing," which is
    honoured as zero winning links, not silently replaced."""
    if selected_links is None:
        winning_links, triggering = _pick_winning_links(execution_rows, citation_ranking_rows, config)
    else:
        winning_links = list(selected_links)
        _, triggering = _collect_cited_urls(execution_rows)
    allow_fallback = selected_links is None

    llm_client = providers.get("ChatGPT")
    llm_key = key_manager.current_key("groq")

    gap_result = a7.run_agent7(
        winning_links, config["brand"], config["competitors"], config["sector"],
        triggering_prompts_by_link=triggering, max_links=config.get("max_winning_links", 3),
        llm_client=llm_client, llm_api_key=llm_key, log=log,
    )
    draft_result = a8.run_agent8(
        execution_rows, citation_ranking_rows, config["brand"], config["competitors"],
        config["sector"], config["region"], max_drafts=config.get("max_drafts", 3),
        link_analyses=gap_result.get("analyses"), triggering_prompts_by_link=triggering,
        allow_fallback=allow_fallback,
        llm_client=llm_client, llm_api_key=llm_key, log=log,
    )
    return gap_result, draft_result


def build_content_gaps_targets(gevs_result, citation_ranking_rows, gap_result, brand):
    brand_stats = next(s for s in gevs_result["main_table"] if s["entity"] == brand)
    numeric_ranks = [r["avg_brand_rank"] for r in citation_ranking_rows if isinstance(r["avg_brand_rank"], (int, float))]
    avg_rank = round(sum(numeric_ranks) / len(numeric_ranks), 1) if numeric_ranks else "Not Cited"
    n_gaps = len(gap_result["gap_rows"])
    quick_wins = sum(1 for g in gap_result["gap_rows"] if g["priority"].startswith("🔴"))
    medium = sum(1 for g in gap_result["gap_rows"] if g["priority"].startswith("🟡"))
    strategic = n_gaps - quick_wins - medium
    return {
        "current_top3_gevs": brand_stats["gevs_top3"],
        "current_avg_citation_rank": avg_rank,
        "content_gaps_identified": n_gaps,
        "recs_generated": n_gaps,
        "quick_wins": quick_wins,
        "medium_term": medium,
        "strategic": strategic,
    }


def run_summary(config, execution_rows):
    """Agent 9."""
    active_llms = config.get("active_llms", ACTIVE_LLMS_DEFAULT)
    return a9.build_rank_improvement_recs(execution_rows, config["brand"], config["competitors"], active_llms)


def run_video_intelligence(store, run_id, config, key_manager, test_set_rows, log=default_log, session=None):
    """Agent 4 — optional. Skips cleanly (returns {}) if no YouTube key is
    configured; the caller doesn't need to branch on that itself."""
    api_key = key_manager.current_key("youtube")
    return a4.run_agent4(store, run_id, test_set_rows, api_key=api_key or "", log=log, session=session)


def run_executive_summary(store, run_id, config, test_set_rows, execution_rows, gevs_result,
                            citation_intel_rows, gap_result, content_gaps_targets, draft_result,
                            providers=None, key_manager=None, polish_with_llm=False, log=default_log):
    """Agent 9 closer + the three previously-untracked PRD success metrics
    (Prompt Relevance, Processing Speed, Citation Accuracy — see kpi.py).
    Zero extra API calls unless polish_with_llm=True, in which case exactly
    one extra call is made for the whole run, with a template fallback on
    any failure (see executive_summary.render_llm_polished_summary)."""
    active_llms = config.get("active_llms", ACTIVE_LLMS_DEFAULT)
    citation_accuracy = kpi.compute_citation_accuracy(execution_rows, active_llms)
    prompt_relevance = kpi.compute_prompt_relevance(store, run_id, test_set_rows)
    processing_speed = kpi.get_processing_speed(store, run_id)

    summary_data = a_exec.build_summary_data(
        config, gevs_result, citation_intel_rows, gap_result, content_gaps_targets, draft_result,
        citation_accuracy, prompt_relevance, processing_speed,
    )

    llm_client = llm_api_key = None
    if polish_with_llm and providers is not None and key_manager is not None:
        llm_client = providers.get("ChatGPT")
        llm_api_key = key_manager.current_key("groq")

    narrative = (
        a_exec.render_llm_polished_summary(summary_data, llm_client=llm_client, llm_api_key=llm_api_key, log=log)
        if polish_with_llm else a_exec.render_template_summary(summary_data)
    )
    summary_data["narrative_markdown"] = narrative

    kpi_data = {
        "citation_accuracy": citation_accuracy,
        "prompt_relevance": prompt_relevance,
        "processing_speed": processing_speed,
    }
    return summary_data, kpi_data


def assemble_workbook(config, prompt_discovery_rows, execution_rows, gevs_result,
                        citation_ranking_rows, citation_intel_rows, gap_result,
                        content_gaps_targets, draft_result, rank_recs_result, run_id,
                        video_results: dict = None, executive_summary_data: dict = None):
    cfg = excel_export.RunConfig(
        run_id=run_id, brand=config["brand"], website=config.get("website", ""),
        sector=config["sector"], region=config["region"],
    )
    wb = excel_export.build_workbook(
        cfg, prompt_discovery_rows, execution_rows, gevs_result, citation_ranking_rows,
        citation_intel_rows, gap_result, content_gaps_targets, draft_result["strategy_rows"],
        rank_recs_result, config["competitors"], video_results=video_results,
        executive_summary_data=executive_summary_data,
    )
    return wb, cfg


def assemble_html(cfg, gevs_result, citation_intel_rows, gap_result, rank_recs_result,
                    execution_rows: list = None, video_results: dict = None,
                    executive_summary_markdown: str = None, kpi_data: dict = None) -> str:
    return html_export.build_html_report(cfg, gevs_result, citation_intel_rows, gap_result, rank_recs_result,
                                            execution_rows=execution_rows, video_results=video_results,
                                            executive_summary_markdown=executive_summary_markdown,
                                            kpi_data=kpi_data)


def assemble_drafts_zip(draft_result: dict, extra_files: dict = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, text in draft_result["drafts"].items():
            zf.writestr(filename, text)
        for filename, text in (extra_files or {}).items():
            zf.writestr(filename, text)
    return buf.getvalue()
