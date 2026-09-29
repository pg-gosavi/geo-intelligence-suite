"""
excel_export.py — writes the final .xlsx, schema-matched to the sample
reference workbook. Sheet names, headers, and metadata-block layout come
straight from src/schema.py, which was extracted programmatically from the
reference file (see tests/test_excel_schema.py for the assertion that keeps
this contract honest).
"""
from __future__ import annotations

import datetime as dt

from openpyxl import Workbook
from openpyxl.styles import Font

from . import schema


def _now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_metadata_block(ws, run_id, brand, website, sector, region, scope, generated):
    ws.append([schema.SHEET_TITLES[ws.title]])
    ws["A1"].font = Font(bold=True, size=12)
    ws.append(["Run ID", run_id, "Brand", brand, "Website", website])
    ws.append(["Sector", sector, "Region", region])
    ws.append(["Scope", scope, "Generated", generated])
    for r in (2, 3, 4):
        ws.cell(row=r, column=1).font = Font(bold=True)
        ws.cell(row=r, column=3).font = Font(bold=True)


def _write_table(ws, headers, rows):
    header_row_idx = ws.max_row + 1
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        ws.cell(row=header_row_idx, column=c).font = Font(bold=True)
    for row in rows:
        ws.append(row)


class RunConfig:
    def __init__(self, run_id, brand, website, sector, region, scope=None, generated=None):
        self.run_id = run_id
        self.brand = brand
        self.website = website
        self.sector = sector
        self.region = region
        self.scope = scope or schema.DEFAULT_SCOPE
        self.generated = generated or _now_str()


def build_workbook(
    cfg: RunConfig,
    prompt_discovery_rows: list,
    execution_rows: list,
    gevs_result: dict,
    citation_ranking_rows: list,
    citation_intel_rows: list,
    content_gaps_result: dict,
    content_gaps_targets: dict,
    content_strategy_rows: list,
    rank_recs_result: dict,
    competitors: list,
    video_results: dict = None,   # optional bonus 9th sheet — {prompt_num: {"videos": [...]}}, only if Agent 4 ran
    executive_summary_data: dict = None,  # from src.agents.executive_summary.build_summary_data
) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)

    _sheet_executive_summary(wb, cfg, executive_summary_data)
    _sheet_prompt_discovery(wb, cfg, prompt_discovery_rows)
    _sheet_llm_execution(wb, cfg, execution_rows)
    _sheet_gevs(wb, cfg, gevs_result)
    _sheet_citation_ranking(wb, cfg, citation_ranking_rows, cfg.brand, competitors)
    _sheet_citation_intel(wb, cfg, citation_intel_rows)
    _sheet_content_gaps(wb, cfg, content_gaps_result, content_gaps_targets)
    _sheet_content_strategy(wb, cfg, content_strategy_rows)
    _sheet_rank_recs(wb, cfg, rank_recs_result)

    if video_results:
        _sheet_video_intelligence(wb, cfg, execution_rows, video_results)

    return wb


def _meta(ws, cfg):
    write_metadata_block(ws, cfg.run_id, cfg.brand, cfg.website, cfg.sector, cfg.region, cfg.scope, cfg.generated)


def _sheet_executive_summary(wb, cfg, summary_data: dict = None):
    """First sheet in the workbook — a KPI dashboard + narrative, built from
    src.agents.executive_summary.build_summary_data. Deliberately tolerant of
    summary_data=None (writes a minimal placeholder) so build_workbook never
    hard-fails when a caller hasn't wired the executive-summary step in yet."""
    ws = wb.create_sheet("Executive Summary")
    _meta(ws, cfg)

    if not summary_data:
        _write_table(ws, schema.HEADERS_EXECUTIVE_SUMMARY, [["Summary", "Not generated this run", "-", "-"]])
        return

    d = summary_data
    rows = [
        ["Brand", d["brand"], "-", "-"],
        ["GEVS Top-3 Visibility", f"{d['brand_gevs_top3']}%", "-", f"Rank #{d['brand_rank']}"],
        ["GEVS Overall", f"{d['brand_gevs_overall']}%", "-", f"{d['brand_total_mentions']} total mentions"],
        ["Category Leader", d["leader_brand"], "-", f"{d['leader_gevs_top3']}%"],
        ["Gap to Leader", f"{d['gap_to_leader_pp']} pp", "0 pp", "Leader" if d["is_leader"] else "Trailing"],
        ["Top Cited Domain", d["top_domain"], "-", f"{d['top_domain_citations']} citations"],
        ["Content Gaps Identified", d["content_gaps_identified"], "-",
         f"{d['quick_wins']} quick wins / {d['medium_term']} medium-term / {d['strategic']} strategic"],
        ["Draft Articles Generated", d["drafts_generated"], "-", "-"],
        ["Citation Accuracy %", d["citation_accuracy_pct"] if d["citation_accuracy_pct"] is not None else "N/A",
         f"{d['citation_accuracy_target_pct']}%",
         "Met" if d["citation_accuracy_meets_target"] else "Not met"],
        ["Prompt Relevance % (user-rated)", d["prompt_relevance_pct"] if d["prompt_relevance_pct"] is not None else "Not rated",
         "-", f"{d['prompt_relevance_rated_count']}/{d['prompt_relevance_total']} prompts rated"],
        ["Processing Speed (elapsed)", f"{int(d['processing_elapsed_s'] // 60)}m {int(d['processing_elapsed_s'] % 60)}s",
         "< 3 min", "Met" if d["processing_meets_target"] else "Not met (free-tier rate limits — see README)"],
    ]
    _write_table(ws, schema.HEADERS_EXECUTIVE_SUMMARY, rows)

    ws.append([])
    ws.append(["Narrative"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    narrative = d.get("narrative_markdown", "")
    for line in narrative.splitlines() or [""]:
        ws.append([line])


def _sheet_prompt_discovery(wb, cfg, rows):
    ws = wb.create_sheet("Prompt Discovery")
    _meta(ws, cfg)
    table_rows = []
    for r in rows:
        table_rows.append([
            r.get("num"), r.get("seed_theme"), r.get("source"), r.get("expanded_query"),
            r.get("intent_category"), r.get("conversational_prompt"), r.get("priority"),
            r.get("include_in_test_set"), r.get("notes"), r.get("execution_status"),
            r.get("agent1_status"), r.get("agent2_status"), r.get("approved_by"),
            r.get("agent2_status"), r.get("agent1_status"),
        ])
    _write_table(ws, schema.HEADERS_PROMPT_DISCOVERY, table_rows)


def _sheet_llm_execution(wb, cfg, execution_rows):
    ws = wb.create_sheet("LLM Execution")
    _meta(ws, cfg)
    table_rows = []
    for r in execution_rows:
        row = [r["num"], r["conversational_prompt"], r["intent_category"]]
        for llm in schema.ALL_LLMS:
            cell = r["per_llm"].get(llm, {})
            entities_mentioned = cell.get("mentioned", {})
            entities_position = cell.get("position", {})
            # LLM Execution reports the BRAND's own mention/position for this response.
            brand = cfg.brand
            mentioned = "Yes" if entities_mentioned.get(brand) else ("No" if entities_mentioned else cell.get("cited_urls"))
            if isinstance(cell.get("cited_urls"), str) and cell.get("cited_urls") in ("Not tested", "Future scope"):
                mentioned = cell["cited_urls"]
                position = cell["cited_urls"]
                cited_urls = cell["cited_urls"]
                domains = cell["cited_urls"]
                status = cell["cited_urls"]
            else:
                mentioned = "Yes" if entities_mentioned.get(brand) else "No"
                position = entities_position.get(brand, "-")
                cited_urls = cell.get("cited_urls", "")
                domains = cell.get("domains", "")
                status = cell.get("source_status", "")
            row += [mentioned, position, cited_urls, domains, status]
        se_urls = r.get("search_evidence_urls", [])
        se_domains = r.get("search_evidence_domains", [])
        row += [" | ".join(se_urls), ", ".join(se_domains)]
        top3 = (se_domains + ["", "", ""])[:3]
        row += top3
        table_rows.append(row)
    _write_table(ws, schema.llm_execution_headers(), table_rows)


def _gevs_placeholder(llm: str) -> str:
    """Same reasoning as agent3's _default_cell: PROVIDER_DEFAULT_STATUS has
    real text only for Claude/Perplexity/Copilot; ChatGPT/Gemini map to None
    there since they're normally active. When either was excluded from this
    run's active_llms, that None must still render as "Not tested" — not a
    literal 0 that reads as "tested, zero mentions."""
    return schema.PROVIDER_DEFAULT_STATUS.get(llm) or "Not tested"


def _sheet_gevs(wb, cfg, gevs_result):
    ws = wb.create_sheet("GEO Visibility Score")
    _meta(ws, cfg)
    main_rows = []
    for s in gevs_result["main_table"]:
        pm = s["per_llm_mentions"]
        main_rows.append([
            s["entity"],
            pm.get("ChatGPT", _gevs_placeholder("ChatGPT")),
            pm.get("Gemini", _gevs_placeholder("Gemini")),
            pm.get("Claude", _gevs_placeholder("Claude")),
            pm.get("Perplexity", _gevs_placeholder("Perplexity")),
            pm.get("Copilot", _gevs_placeholder("Copilot")),
            s["total_mentions"], s["mention_rate"], s["gevs_overall"], s["gevs_top3"],
            s["avg_position"], s["vs_gap"], s["rank"], s["status"],
        ])
    _write_table(ws, schema.HEADERS_GEVS_MAIN, main_rows)

    ws.append([])
    brand = gevs_result["brand"]
    ws.append([f"{brand} — Per LLM GEVS Breakdown"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    plt = gevs_result["per_llm_table"]
    header_row_idx = ws.max_row + 1
    ws.append(schema.HEADERS_GEVS_PER_LLM)
    for c in range(1, len(schema.HEADERS_GEVS_PER_LLM) + 1):
        ws.cell(row=header_row_idx, column=c).font = Font(bold=True)

    def fmt(llm, mapping, is_pct=False):
        v = mapping.get(llm)
        if v is None:
            return _gevs_placeholder(llm)
        return f"{v}%" if is_pct else v

    ws.append(["Mentions"] + [fmt(l, plt["mentions"]) for l in schema.ALL_LLMS] + [""])
    ws.append(["GEVS %"] + [fmt(l, plt["gevs_pct"], is_pct=True) for l in schema.ALL_LLMS] + [plt["interpretation"]])


def _sheet_citation_ranking(wb, cfg, rows, brand, competitors):
    ws = wb.create_sheet("Citation Ranking")
    _meta(ws, cfg)
    entities = [brand] + list(competitors)
    table_rows = []
    for r in rows:
        row = [r["num"], r["conversational_prompt"], r["intent_category"], r["avg_brand_rank"]]
        for llm in schema.ALL_LLMS:
            for entity in entities:
                pos, src = r["per_llm_entity"].get((llm, entity), ("-", "Not cited for this prompt/model"))
                row += [pos, src]
        table_rows.append(row)
    _write_table(ws, schema.citation_ranking_headers(brand, competitors), table_rows)


def _sheet_citation_intel(wb, cfg, rows):
    ws = wb.create_sheet("Citation Intel")
    _meta(ws, cfg)
    table_rows = []
    for r in rows:
        pl = r["per_llm"]
        table_rows.append([
            r["rank"], r["domain"], r["source_quality"], r["total_citations"],
            pl.get("ChatGPT", 0), pl.get("Gemini", 0), pl.get("Claude", "Not tested"),
            pl.get("Perplexity", "Not tested"), pl.get("Copilot", "Future scope"),
            r["priority_action"],
        ])
    _write_table(ws, schema.HEADERS_CITATION_INTEL, table_rows)


def _sheet_content_gaps(wb, cfg, content_gaps_result, targets):
    ws = wb.create_sheet("Content Gaps & Recommendations")
    _meta(ws, cfg)
    ws.append(["GEVS IMPROVEMENT TARGETS"])
    ws.cell(row=ws.max_row, column=1).font = Font(bold=True)
    header_row_idx = ws.max_row + 1
    ws.append(schema.HEADERS_CONTENT_GAPS_TARGETS)
    for c in range(1, len(schema.HEADERS_CONTENT_GAPS_TARGETS) + 1):
        ws.cell(row=header_row_idx, column=c).font = Font(bold=True)
    ws.append([
        targets["current_top3_gevs"], targets["current_avg_citation_rank"],
        targets["content_gaps_identified"], targets["recs_generated"],
        targets["quick_wins"], targets["medium_term"], targets["strategic"],
        "Addendum scope - not generated", "Addendum scope - not generated",
        "Addendum scope - not generated", "Addendum scope - not generated",
    ])
    ws.append([])
    table_rows = []
    for r in content_gaps_result["gap_rows"]:
        table_rows.append([
            r["num"], r["issue"], r["triggering_prompts"], r["signal_source"],
            r["competitor_benchmark"], r["recommendation"], r["content_type"], r["priority"],
            r["timeline"], r["target_llms"], r["impact_note"], r["status"],
            r["remediation_source_domain"], r["remediation_source_role"], r["remediation_brief_snippet"],
        ])
    _write_table(ws, schema.HEADERS_CONTENT_GAPS_ISSUES, table_rows)


def _sheet_content_strategy(wb, cfg, rows):
    ws = wb.create_sheet("Content Strategy")
    _meta(ws, cfg)
    table_rows = []
    for r in rows:
        table_rows.append([
            r["num"], r["title_brief"], r["content_type"], r["target_prompts"],
            r["primary_intent"], r["competitor_to_outperform"], r["target_llm_citation"], r["priority"],
        ])
    _write_table(ws, schema.HEADERS_CONTENT_STRATEGY, table_rows)


def _sheet_rank_recs(wb, cfg, rank_recs_result):
    ws = wb.create_sheet("Rank Improvement Recs")
    _meta(ws, cfg)
    s = rank_recs_result["summary"]
    table_rows = [[
        s["prompt_num"], s["prompt"], s["intent"], s["llm"], s["current_brand_rank"],
        s["leader_brand"], s["leader_rank"], s["rank_gap"], s["suggested_lever"],
        s["root_cause"], s["recommended_action"], s["target_6m"], s["target_12m"], s["rank_status"],
    ]]
    for r in rank_recs_result["rows"]:
        table_rows.append([
            r["prompt_num"], r["prompt"], r["intent"], r["llm"], r["current_brand_rank"],
            r["leader_brand"], r["leader_rank"], r["rank_gap"], r["suggested_lever"],
            r["root_cause"], r["recommended_action"], r["target_6m"], r["target_12m"], r["rank_status"],
        ])
    _write_table(ws, schema.HEADERS_RANK_IMPROVEMENT_RECS, table_rows)


def _sheet_video_intelligence(wb, cfg, execution_rows, video_results):
    """Bonus sheet (Agent 4) — only added when a YouTube Data API key was
    configured and returned results. Uses SHEET_TITLES['Video Intelligence']
    as its own metadata title, same block layout as every other sheet."""
    ws = wb.create_sheet(schema.VIDEO_INTELLIGENCE_SHEET)
    _meta(ws, cfg)
    brand_lower = cfg.brand.lower()
    table_rows = []
    for row in execution_rows:
        videos = (video_results.get(row["num"]) or {}).get("videos", [])
        for rank, v in enumerate(videos, start=1):
            table_rows.append([
                row["num"], row["conversational_prompt"], rank, v.get("title", ""), v.get("url", ""),
                "Yes" if brand_lower in (v.get("title", "") or "").lower() else "No",
            ])
    _write_table(ws, schema.HEADERS_VIDEO_INTELLIGENCE, table_rows)


def save_workbook(wb: Workbook, path: str):
    wb.save(path)
