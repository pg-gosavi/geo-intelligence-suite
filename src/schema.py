"""
Ground-truth schema for the GEO Intelligence Suite workbook.

Every sheet name, column header, and status-vocabulary string in this file
was extracted programmatically from the attached reference workbook
(uti-mutual-fund-india-2026-08-27-0734-geo-report.xlsx) — see
tests/fixtures/sample_reference.xlsx and tests/test_excel_schema.py, which
assert generated output matches this contract exactly.

Do not hand-edit headers without re-verifying against the reference file.
"""

SHEET_NAMES = [
    "Executive Summary",
    "Prompt Discovery",
    "LLM Execution",
    "GEO Visibility Score",
    "Citation Ranking",
    "Citation Intel",
    "Content Gaps & Recommendations",
    "Content Strategy",
    "Rank Improvement Recs",
]

# The original reference workbook's own 8 sheets, unmodified — used only to
# assert fidelity to the source file itself (tests/test_excel_schema.py's
# test_sheet_names_match_fixture_exactly). SHEET_NAMES above is what this
# build actually generates: the original 8 plus the new Executive Summary
# sheet requested on top of the PRD's original schema.
ORIGINAL_REFERENCE_SHEET_NAMES = [n for n in SHEET_NAMES if n != "Executive Summary"]

# Deliberate, documented deviation from the original reference workbook's
# 8-sheet contract: "Executive Summary" is a new deliverable (KPI dashboard +
# narrative) added on top of the PRD's original schema, not part of it. See
# TESTING_REPORT.md for why this is safe to add without breaking the
# original 8-sheet fidelity tests — those now assert Executive Summary is
# present *in addition to*, not instead of, the original eight.
HEADERS_EXECUTIVE_SUMMARY = [
    "Metric", "Value", "Target", "Status",
]

SHEET_TITLES = {
    "Executive Summary": "GEO Intelligence Suite — Executive Summary",
    "Prompt Discovery": "GEO Intelligence Suite — Prompt Discovery",
    "LLM Execution": "GEO Intelligence Suite — LLM Execution",
    "GEO Visibility Score": "GEO Intelligence Suite — Visibility Score",
    "Citation Ranking": "GEO Intelligence Suite — Citation Ranking",
    "Citation Intel": "GEO Intelligence Suite — Citation Intel",
    "Content Gaps & Recommendations": "GEO Intelligence Suite — Content Gaps & Recommendations",
    "Content Strategy": "GEO Intelligence Suite — Content Strategy",
    "Rank Improvement Recs": "GEO Intelligence Suite — Rank Improvement Recs",
    "Video Intelligence": "GEO Intelligence Suite — Video Intelligence (bonus, Agent 4)",
}

# Optional 9th sheet (Agent 4 / YouTube). Not part of the reference
# workbook's 8-sheet contract — appended only when a YouTube Data API key
# was actually configured and returned results, so a run without one keeps
# exactly the 8 required sheets in test_excel_schema.py.
VIDEO_INTELLIGENCE_SHEET = "Video Intelligence"
HEADERS_VIDEO_INTELLIGENCE = [
    "#", "Conversational Prompt (LLM-Ready)", "Video Rank", "Video Title", "Video URL", "Brand Mentioned in Title?",
]

# The 3-row metadata block that appears above the data table on every sheet.
# Rendered by excel_export.write_metadata_block() as:
#   Row 1: ["Run ID", <run_id>, "Brand", <brand>, "Website", <website>]
#   Row 2: ["Sector", <sector>, "Region", <region>]
#   Row 3: ["Scope", <scope>, "Generated", <generated_ts>]
METADATA_FIELD_ORDER = [
    ("Run ID", "run_id"),
    ("Brand", "brand"),
    ("Website", "website"),
    ("Sector", "sector"),
    ("Region", "region"),
    ("Scope", "scope"),
    ("Generated", "generated"),
]

DEFAULT_SCOPE = "GEO Intelligence Suite — Measurement + Light Strategy"

# ---------------------------------------------------------------------------
# Providers tracked throughout the workbook, in the sample's own column order.
# ---------------------------------------------------------------------------
ALL_LLMS = ["ChatGPT", "Gemini", "Claude", "Perplexity", "Copilot"]

# Providers this free-tier build actually calls. Everything else in ALL_LLMS
# renders using PROVIDER_DEFAULT_STATUS below, exactly as the sample does.
ACTIVE_LLMS_DEFAULT = ["ChatGPT"]

# Sample behaviour: Claude/Perplexity columns read "Not tested" throughout;
# Copilot reads "Future scope" throughout (no public consumer API exists).
PROVIDER_DEFAULT_STATUS = {
    "ChatGPT": None,       # actively executed by default
    "Gemini": None,        # actively executed by default
    "Claude": "Not tested",
    "Perplexity": "Not tested",
    "Copilot": "Future scope",
}

# ---------------------------------------------------------------------------
# Verbatim status vocabulary, reused so output matches the reference format.
# ---------------------------------------------------------------------------
SOURCE_STATUS_EXACT_URLS = "Exact non-homepage URLs returned by model"
SOURCE_STATUS_DOMAINS_ONLY = "Domains only - no matching page URLs found"
SOURCE_STATUS_NO_URLS_TEXT = "No exact page URLs available"  # goes in the *Cited URLs* cell
SOURCE_STATUS_NOT_TESTED = "Not tested"
SOURCE_STATUS_FUTURE_SCOPE = "Future scope"

CITATION_STATUS_NOT_CITED_PROMPT = "Not cited for this prompt/model"
CITATION_STATUS_CITED_NO_URL = "Cited, but no entity-specific source URL returned"
CITATION_STATUS_NOT_CITED_RANK = "Not Cited"  # used in the "Avg Brand Rank" / rank cells

SOURCE_QUALITY_SUPPORTING_SIGNAL = "supporting_signal"          # kept for backward-compat / sample-vocabulary reference
SOURCE_QUALITY_APP_DISTRIBUTION = "app_distribution_signal"
SOURCE_QUALITY_WEAK_SUPPORTING = "Weak/supporting evidence"      # kept, but now reserved for genuine UGC/forum content

# Finer-grained tiers added on top of the sample's original 3-value
# vocabulary above (see agent6_citation_ranking.py) — none of these change
# the xlsx SCHEMA (Citation Intel's headers are unchanged), only the label
# text written into the "Source Quality" cell, which was never a fixed
# enum in the reference workbook to begin with.
SOURCE_QUALITY_OFFICIAL_BRAND = "official_brand_source"          # the tested brand's or a competitor's own site
SOURCE_QUALITY_REGULATORY = "regulatory_signal"                  # government/regulator domains
SOURCE_QUALITY_EDITORIAL_AUTHORITY = "editorial_authority_signal"  # recognized comparison/review/news authorities
SOURCE_QUALITY_VIDEO = "video_signal"                            # youtube.com etc. — quality varies by channel
SOURCE_QUALITY_UNCLASSIFIED = "unclassified_signal"              # not yet categorized — needs manual review, NOT a confident "weak" verdict

# ---------------------------------------------------------------------------
# Headers — copied verbatim from the reference workbook.
# ---------------------------------------------------------------------------

HEADERS_PROMPT_DISCOVERY = [
    "#", "Seed Prompt Theme", "Source (Auto/PAA/Related)", "Expanded Raw Query",
    "Intent Category", "Conversational Prompt (LLM-Ready)", "Priority (H/M/L)",
    "Include in Test Set?", "Notes", "Execution Status", "Agent 1 Status",
    "Agent 2 Status", "Approved By", "Agent 2 Status", "Agent 1 Status",
]

HEADERS_CONTENT_STRATEGY = [
    "#", "Article Title Content Brief", "Content Type", "Target Prompts Covered",
    "Primary Intent", "Competitor to Outperform", "Target LLM Citation", "Priority",
]

HEADERS_RANK_IMPROVEMENT_RECS = [
    "Prompt #", "Prompt", "Intent", "LLM", "Current Brand Rank", "Leader Brand",
    "Leader Rank", "Rank Gap", "Suggested Lever", "Root Cause", "Recommended Action",
    "Suggested Target Rank (6M)", "Suggested Target Rank (12M)", "Rank Status",
]

HEADERS_CITATION_INTEL = [
    "Rank", "Domain", "Source Quality", "Total Citations (All LLMs)",
    "ChatGPT Citations", "Gemini Citations", "Claude Citations",
    "Perplexity Citations", "Copilot Citations", "Priority Action",
]

HEADERS_GEVS_MAIN = [
    "Brand", "ChatGPT Mentions", "Gemini Mentions", "Claude Mentions",
    "Perplexity Mentions", "Copilot Mentions", "Total Mentions", "Mention Rate %",
    "GEVS Overall %", "GEVS Top-3 Visibility %", "Avg Position (when cited)",
    "vs Brand GEVS Gap", "Rank", "Status",
]

HEADERS_GEVS_PER_LLM = ["Metric", "ChatGPT", "Gemini", "Claude", "Perplexity", "Copilot", "INTERPRETATION"]

HEADERS_CONTENT_GAPS_TARGETS = [
    "Current Top-3 GEVS %", "Current Avg Citation Rank", "Content Gaps Identified",
    "Recs Generated", "Quick Wins (≤30 days)", "Medium-term (31–90 days)",
    "Strategic (90+ days)", "Target GEVS (6M)", "Target GEVS (12M)",
    "Target Citation Rank (6M)", "Est. GEVS Lift",
]

HEADERS_CONTENT_GAPS_ISSUES = [
    "#", "Content Gap Issue Identified", "Triggering Prompts", "Signal Source (Where Found)",
    "Competitor Benchmark", "Recommendation", "Content Type", "Priority", "Timeline",
    "Target LLM(s)", "Impact Note", "Status", "Remediation Source Domain",
    "Remediation Source Role", "Remediation Brief Snippet",
]


def llm_execution_headers():
    headers = ["#", "Conversational Prompt (LLM-Ready)", "Intent Category"]
    for llm in ALL_LLMS:
        headers += [
            f"{llm} Brand Mentioned?", f"{llm} Brand Position", f"{llm} Cited URLs",
            f"{llm} Supporting Domains", f"{llm} Source Status",
        ]
    headers += [
        "Search Evidence URLs", "Search Evidence Domains",
        "TOP CITED DOMAINS Domain 1", "TOP CITED DOMAINS Domain 2", "TOP CITED DOMAINS Domain 3",
    ]
    return headers


def citation_ranking_headers(brand, competitors):
    """brand + competitors define the entity column groups, per-LLM, in that order."""
    entities = [brand] + list(competitors)
    headers = ["#", "Conversational Prompt (LLM-Ready)", "Intent", "Avg Brand Rank"]
    for llm in ALL_LLMS:
        for entity in entities:
            headers += [f"{llm} {entity}", f"{llm} {entity} Source URLs"]
    return headers
