import os
import sys

from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import excel_export, schema
from src.agents import agent5_visibility_score as a5

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample_reference.xlsx")


def _fixture_headers():
    """Extract sheet names + header rows straight from the reference workbook,
    independent of src/schema.py, so this test can't pass just because both
    sides read from the same hand-copied constant."""
    wb = load_workbook(FIXTURE, read_only=True, data_only=True)
    out = {}
    for name in wb.sheetnames:
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
        if name in ("GEO Visibility Score", "Content Gaps & Recommendations"):
            # these two sheets have a second header row further down
            out[name] = {"main_header": list(rows[4])}
        else:
            out[name] = {"main_header": list(rows[4])}
    return out


def test_sheet_names_match_fixture_exactly():
    wb = load_workbook(FIXTURE, read_only=True, data_only=True)
    assert list(wb.sheetnames) == schema.ORIGINAL_REFERENCE_SHEET_NAMES


def test_generated_workbook_includes_original_eight_plus_executive_summary():
    """This build adds an Executive Summary sheet on top of the PRD's
    original 8-sheet contract — this asserts it's additive, not a
    replacement: every original sheet name is still present, in its
    original relative order, with Executive Summary prepended."""
    assert schema.SHEET_NAMES[0] == "Executive Summary"
    assert schema.SHEET_NAMES[1:] == schema.ORIGINAL_REFERENCE_SHEET_NAMES


def test_prompt_discovery_headers_match_fixture():
    fixture = _fixture_headers()
    assert fixture["Prompt Discovery"]["main_header"] == schema.HEADERS_PROMPT_DISCOVERY


def test_llm_execution_headers_match_fixture():
    fixture = _fixture_headers()
    assert fixture["LLM Execution"]["main_header"] == schema.llm_execution_headers()


def test_gevs_main_headers_match_fixture():
    fixture = _fixture_headers()
    assert fixture["GEO Visibility Score"]["main_header"] == schema.HEADERS_GEVS_MAIN


def test_citation_intel_headers_match_fixture():
    fixture = _fixture_headers()
    assert fixture["Citation Intel"]["main_header"] == schema.HEADERS_CITATION_INTEL


def test_content_strategy_headers_match_fixture():
    fixture = _fixture_headers()
    assert fixture["Content Strategy"]["main_header"] == schema.HEADERS_CONTENT_STRATEGY


def test_rank_improvement_recs_headers_match_fixture():
    fixture = _fixture_headers()
    assert fixture["Rank Improvement Recs"]["main_header"] == schema.HEADERS_RANK_IMPROVEMENT_RECS


def test_citation_ranking_headers_shape_matches_fixture_for_same_entity_count():
    """The fixture's Citation Ranking header depends on brand+competitor
    names (UTI Mutual Fund + 5 named competitors); we check our header
    *generator* reproduces that exact header when fed the same entities."""
    wb = load_workbook(FIXTURE, read_only=True, data_only=True)
    ws = wb["Citation Ranking"]
    rows = list(ws.iter_rows(values_only=True))
    fixture_header = list(rows[4])

    brand = "UTI Mutual Fund"
    competitors = ["Axis Mutual Fund", "Nippon India Mutual Fund", "SBI Mutual Fund",
                   "ICICI Prudential Mutual Fund", "HDFC Mutual Fund"]
    generated_header = schema.citation_ranking_headers(brand, competitors)
    assert generated_header == fixture_header


def _minimal_generated_workbook():
    """Build a tiny end-to-end workbook via excel_export using synthetic data,
    then assert its sheet names + headers against the schema module (the
    actual generation code path, not just the schema constants)."""
    brand = "Acme Fund"
    competitors = ["Beta Fund"]
    execution_rows = [{
        "num": 1, "conversational_prompt": "Best fund?", "intent_category": "recommendation",
        "per_llm": {
            "ChatGPT": {"mentioned": {brand: True, "Beta Fund": False}, "position": {brand: "#1", "Beta Fund": "-"},
                        "cited_urls": "https://acme.example.com/page", "domains": "acme.example.com",
                        "source_status": schema.SOURCE_STATUS_EXACT_URLS},
            "Gemini": {"mentioned": {brand: False, "Beta Fund": True}, "position": {brand: "-", "Beta Fund": "#1"},
                       "cited_urls": schema.SOURCE_STATUS_NO_URLS_TEXT, "domains": "",
                       "source_status": schema.SOURCE_STATUS_DOMAINS_ONLY},
            "Claude": {"mentioned": {}, "position": {}, "cited_urls": "Not tested", "domains": "Not tested",
                       "source_status": "Not tested"},
            "Perplexity": {"mentioned": {}, "position": {}, "cited_urls": "Not tested", "domains": "Not tested",
                           "source_status": "Not tested"},
            "Copilot": {"mentioned": {}, "position": {}, "cited_urls": "Future scope", "domains": "Future scope",
                        "source_status": "Future scope"},
        },
        "search_evidence_urls": ["https://search-evidence.example.com/x"],
        "search_evidence_domains": ["search-evidence.example.com"],
    }]
    prompt_discovery_rows = [{
        "num": 1, "seed_theme": "seed", "source": "seed", "expanded_query": "Best fund?",
        "intent_category": "recommendation", "conversational_prompt": "Best fund?", "priority": "H",
        "include_in_test_set": "Yes", "notes": "", "execution_status": "Executed",
        "agent1_status": "Generated", "agent2_status": "Done", "approved_by": "Client review",
    }]
    gevs_result = a5.run_agent5(execution_rows, brand, competitors, ["ChatGPT", "Gemini"])
    from src.agents import agent6_citation_ranking as a6
    citation_ranking_rows = a6.build_citation_ranking(execution_rows, brand, competitors, ["ChatGPT", "Gemini"])
    citation_intel_rows = a6.build_citation_intel(execution_rows, brand, ["ChatGPT", "Gemini"])
    gap_result = {"gap_rows": []}
    targets = {"current_top3_gevs": 100, "current_avg_citation_rank": 1, "content_gaps_identified": 0,
               "recs_generated": 0, "quick_wins": 0, "medium_term": 0, "strategic": 0}
    draft_result = {"strategy_rows": [], "drafts": {}}
    from src.agents import agent9_summary_export as a9
    rank_recs_result = a9.build_rank_improvement_recs(execution_rows, brand, competitors, ["ChatGPT", "Gemini"])

    cfg = excel_export.RunConfig(run_id="schema-test", brand=brand, website="https://acme.example.com",
                                   sector="mutual_funds", region="India")
    wb = excel_export.build_workbook(
        cfg, prompt_discovery_rows, execution_rows, gevs_result, citation_ranking_rows,
        citation_intel_rows, gap_result, targets, draft_result["strategy_rows"], rank_recs_result, competitors,
    )
    return wb


def test_generated_workbook_has_all_eight_sheets_in_order():
    wb = _minimal_generated_workbook()
    assert list(wb.sheetnames) == schema.SHEET_NAMES


def test_generated_workbook_metadata_block_present_on_every_sheet():
    wb = _minimal_generated_workbook()
    for name in schema.SHEET_NAMES:
        ws = wb[name]
        assert ws.cell(row=1, column=1).value == schema.SHEET_TITLES[name]
        assert ws.cell(row=2, column=1).value == "Run ID"
        assert ws.cell(row=2, column=3).value == "Brand"
        assert ws.cell(row=3, column=1).value == "Sector"
        assert ws.cell(row=4, column=1).value == "Scope"


def test_generated_workbook_opens_and_is_well_formed(tmp_path=None):
    import tempfile
    wb = _minimal_generated_workbook()
    path = os.path.join(tempfile.mkdtemp(), "out.xlsx")
    wb.save(path)
    reopened = load_workbook(path)
    assert list(reopened.sheetnames) == schema.SHEET_NAMES
