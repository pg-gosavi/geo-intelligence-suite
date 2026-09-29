"""
End-to-end test: runs the complete pipeline (Agents 1-9 + exports) against
fake LLM/search responses — zero real API calls — and confirms it produces
a valid, openable .xlsx and a valid, browser-openable .html, with no
exceptions and no missing sheets. Also covers the edge cases called out in
the build spec: empty competitor list, a brand name with special
characters, zero citations for every model on a prompt, a malformed/non-
JSON LLM response, and non-ASCII location input.
"""
import os
import sys
import tempfile

from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import key_manager as km
from src import pipeline
from src import schema
from src import state_store as ss
from tests.fakes import FakeLLMProvider, FakeSearchClient


def _base_config(**overrides):
    config = {
        "brand": "Acme Fund", "website": "https://acme.example.com",
        "sector": "mutual_funds", "region": "India",
        "seed_prompts": ["Best mutual funds for beginners", "Acme Fund vs Beta Fund"],
        "competitors": ["Beta Fund", "Gamma Fund"],
        "active_llms": ["ChatGPT"],
        "max_test_set": 10, "max_winning_links": 2, "max_drafts": 2,
    }
    config.update(overrides)
    return config


def _run_full_pipeline(config, providers=None, key_mgr=None, run_id="e2e-run"):
    base_dir = tempfile.mkdtemp()
    store = ss.StateStore(ss.db_path_for_run(run_id, base_dir=base_dir))
    store.create_run(run_id, config)

    if providers is None:
        canned_gpt = {p: (f"{config['brand']} is a solid option. See https://acme.example.com/{i}", [f"https://acme.example.com/{i}"])
                      for i, p in enumerate(config["seed_prompts"])}
        canned_gemini = {p: (f"{config['brand']} and competitors are both reasonable.", [])
                         for p in config["seed_prompts"]}
        providers = {
            "ChatGPT": FakeLLMProvider("ChatGPT", canned_gpt),
            "Gemini": FakeLLMProvider("Gemini", canned_gemini),
            "search": FakeSearchClient(paa=["related question?"], organic_urls=["https://evidence.example.com/a"]),
        }
    if key_mgr is None:
        key_mgr = km.KeyManager()
        key_mgr.set_keys("groq", "fake-groq-key")

    pd_rows = pipeline.run_prompt_discovery(store, run_id, config, providers, key_mgr, log=lambda m: None)
    test_set = [r for r in pd_rows if r["include_in_test_set"] == "Yes"]
    exec_rows, run_status = pipeline.run_execution(store, run_id, config, providers, key_mgr, test_set, log=lambda m: None)
    gevs, citrank, citintel = pipeline.run_analytics(config, exec_rows)
    gap_result, draft_result = pipeline.run_content_remediation(config, exec_rows, citrank, providers, key_mgr, log=lambda m: None)
    targets = pipeline.build_content_gaps_targets(gevs, citrank, gap_result, config["brand"])
    rank_recs = pipeline.run_summary(config, exec_rows)
    wb, cfg = pipeline.assemble_workbook(config, pd_rows, exec_rows, gevs, citrank, citintel,
                                           gap_result, targets, draft_result, rank_recs, run_id)
    html_report = pipeline.assemble_html(cfg, gevs, citintel, gap_result, rank_recs)
    drafts_zip = pipeline.assemble_drafts_zip(draft_result)
    store.close()
    return {
        "pd_rows": pd_rows, "test_set": test_set, "exec_rows": exec_rows, "run_status": run_status,
        "gevs": gevs, "citrank": citrank, "citintel": citintel, "gap_result": gap_result,
        "draft_result": draft_result, "targets": targets, "rank_recs": rank_recs,
        "workbook": wb, "html": html_report, "drafts_zip": drafts_zip, "base_dir": base_dir,
    }


def test_full_pipeline_runs_without_exceptions_and_produces_valid_outputs():
    result = _run_full_pipeline(_base_config())
    assert result["run_status"] in ("active", "complete")
    assert len(result["exec_rows"]) > 0

    out_path = os.path.join(result["base_dir"], "report.xlsx")
    result["workbook"].save(out_path)
    reopened = load_workbook(out_path)
    assert list(reopened.sheetnames) == schema.SHEET_NAMES
    for name in schema.SHEET_NAMES:
        ws = reopened[name]
        assert ws.max_row >= 5  # title + 3 meta rows + header at minimum

    assert "<html" in result["html"].lower()
    assert "</html>" in result["html"].lower()
    assert result["html"].count("<script") >= 1  # inline JS only, no external assets
    assert "src=\"http" not in result["html"]  # no external script/asset references
    assert "acme fund" in result["html"].lower() or "Acme Fund" in result["html"]

    assert len(result["drafts_zip"]) > 0


def test_empty_competitor_list_does_not_crash():
    config = _base_config(competitors=[])
    result = _run_full_pipeline(config)
    assert result["gevs"]["main_table"][0]["entity"] == "Acme Fund"
    out_path = os.path.join(result["base_dir"], "report.xlsx")
    result["workbook"].save(out_path)
    reopened = load_workbook(out_path)
    assert list(reopened.sheetnames) == schema.SHEET_NAMES


def test_brand_name_with_special_characters():
    config = _base_config(brand="Acme & Co. Fund (India) — \"Premium\" 5%")
    providers = {
        "ChatGPT": FakeLLMProvider("ChatGPT", {}),  # no canned responses -> generic fallback text everywhere
        "Gemini": FakeLLMProvider("Gemini", {}),
        "search": FakeSearchClient(),
    }
    key_mgr = km.KeyManager()
    key_mgr.set_keys("groq", "k1")
    result = _run_full_pipeline(config, providers=providers, key_mgr=key_mgr, run_id="special-chars-run")

    out_path = os.path.join(result["base_dir"], "report.xlsx")
    result["workbook"].save(out_path)
    reopened = load_workbook(out_path)
    assert list(reopened.sheetnames) == schema.SHEET_NAMES
    # brand name in metadata block must survive round-trip unescaped-and-broken
    ws = reopened["Prompt Discovery"]
    assert ws.cell(row=2, column=4).value == config["brand"]
    assert "<html" in result["html"].lower()  # html-escaping must not blow up the report


def test_zero_citations_for_every_model_renders_not_cited_not_crash():
    """No provider returns any URL for any prompt — every Source Status cell
    must read as a clean 'not cited' state, never crash or leave a blank."""
    config = _base_config()
    providers = {
        "ChatGPT": FakeLLMProvider("ChatGPT", {p: ("No specific brand recommendation.", []) for p in config["seed_prompts"]}),
        "Gemini": FakeLLMProvider("Gemini", {p: ("No specific brand recommendation.", []) for p in config["seed_prompts"]}),
        "search": FakeSearchClient(),
    }
    key_mgr = km.KeyManager()
    key_mgr.set_keys("groq", "k1")
    result = _run_full_pipeline(config, providers=providers, key_mgr=key_mgr, run_id="zero-citation-run")

    for row in result["exec_rows"]:
        cell = row["per_llm"]["ChatGPT"]
        assert cell["source_status"] == schema.SOURCE_STATUS_DOMAINS_ONLY
        assert cell["cited_urls"] == schema.SOURCE_STATUS_NO_URLS_TEXT

    out_path = os.path.join(result["base_dir"], "report.xlsx")
    result["workbook"].save(out_path)
    load_workbook(out_path)  # must open cleanly


def test_malformed_llm_response_does_not_crash_run():
    """A provider that raises a generic (non-quota) error for one prompt must
    mark just that cell failed_other and let the run continue — not crash."""
    class MalformedThenFineProvider:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, api_key, **kwargs):
            self.calls += 1
            if self.calls == 1:
                from src.providers.base import ProviderError
                raise ProviderError("Unexpected non-JSON / malformed response body")
            from src.providers.base import ProviderResponse
            return ProviderResponse(text="Acme Fund is a fine choice.", cited_urls=[], model="ChatGPT")

    config = _base_config()
    providers = {
        "ChatGPT": MalformedThenFineProvider(),
        "Gemini": FakeLLMProvider("Gemini", {}),
        "search": FakeSearchClient(),
    }
    key_mgr = km.KeyManager()
    key_mgr.set_keys("groq", "k1")
    result = _run_full_pipeline(config, providers=providers, key_mgr=key_mgr, run_id="malformed-run")
    assert len(result["exec_rows"]) == len(result["test_set"])  # run completed, nothing lost
    out_path = os.path.join(result["base_dir"], "report.xlsx")
    result["workbook"].save(out_path)
    load_workbook(out_path)


def test_non_ascii_location_input():
    config = _base_config(region="भारत (India)", sector="म्यूचुअल फंड")
    result = _run_full_pipeline(config, run_id="non-ascii-run")
    out_path = os.path.join(result["base_dir"], "report.xlsx")
    result["workbook"].save(out_path)
    reopened = load_workbook(out_path)
    ws = reopened["Prompt Discovery"]
    assert ws.cell(row=3, column=4).value == config["region"]
    assert "भारत" in result["html"]
