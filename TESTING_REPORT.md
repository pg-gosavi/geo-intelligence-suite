# Testing Report — GEO Intelligence Suite (Free-Tier Edition)

**Result: 136/136 automated tests passing.** Run yourself with `pytest tests/ -v`.

## Update: fixes and additions from a real production run (HDFC Bank / credit cards)

This is a Groq (gpt-oss-120b) + Gemini fork of the original build. A real
run surfaced several gaps neither the mocked test suite nor the earlier
Gemini/OpenAI live run had exercised — specifically, running with only ONE
LLM active (ChatGPT/Groq), and a non-browsing model that structurally
cannot produce a real citation URL.

1. **Any excluded LLM rendered as a bare `0`, not "Not tested."** The
   placeholder-text logic only had real strings hardcoded for
   Claude/Perplexity/Copilot, on the assumption ChatGPT+Gemini are always
   active together. Once a user ran ChatGPT-only, Gemini's `0` looked
   identical to "tested, zero mentions." Fixed in `agent5`, `agent3`
   (`_default_cell`, plus three call sites that hardcoded `"Claude"` as a
   fallback regardless of which LLM was actually being processed), `agent6`,
   and `excel_export`. 12 new tests in
   `tests/test_partial_active_llms_and_source_quality.py`.
2. **Citation Intel's source-quality classifier was mutual-fund-only.**
   Every domain that wasn't a SEBI/AMFI-style regulator — including the
   tested bank's own site, real editorial authorities (NerdWallet,
   paisabazaar.com), and genuine Reddit/Quora UGC — fell into one generic
   "Weak/supporting evidence" bucket. Rewrote `agent6_citation_ranking.py`
   with official-brand/regulatory/editorial-authority/video/app-store/UGC
   tiers, plus a `llm_cited` flag distinguishing real LLM citations from
   search-evidence-only domains. 8 new tests.
3. **Every "Cited URLs" cell read "No exact page URLs available."** Root
   cause: a non-browsing model (gpt-oss via Groq) can only "cite" a URL by
   guessing from training data, and honest verification correctly rejects
   an unreachable guess. Implemented search-grounded prompting
   (`build_grounded_prompt` in `agent3_llm_execution.py`): the model is now
   given real, already-fetched search results to cite from instead of being
   asked to recall one from memory. 5 new tests in `test_search_grounding.py`.
4. **A fixed per-call delay can't defend a token-per-minute budget.** Groq's
   free tier caps at 30 requests/minute AND 8,000 tokens/minute — a handful
   of longer (now-grounded) prompts can exhaust the token budget while
   comfortably under the request budget, which no fixed-delay pacer can see.
   Added `src/rate_limiter.py`: a sliding-window limiter tracking real
   per-call token usage (from the provider's own `usage` report) against
   both RPM/TPM (short window) and RPD/TPD (daily) budgets, with proactive
   pacing before each call and a pre-emptive "daily budget exceeded" check
   that reuses the exact same rotate/pause machinery as a real 429. 12 new
   tests in `test_rate_limiter.py`, including one exercising the
   agent3-level integration.
5. **Agent 4 (YouTube) was fully implemented but never called.** Wired it
   into `pipeline.py` and `app.py`; when a YouTube key is configured, it now
   feeds a bonus 9th "Video Intelligence" xlsx sheet (only appended when
   video data exists, so the required 8-sheet schema is byte-identical when
   it doesn't) and an HTML report section. 8 new tests in
   `test_video_intelligence_wiring.py`.
6. **"Execution Status" in Prompt Discovery was stuck at "Not executed"**
   even after a successful run — it's set once by Agent 2 and never updated.
   Fixed in `pipeline.run_execution` to reflect real per-prompt attempt
   status (mutating the same row objects `pd_rows` holds).

None of these fixes touch the required 8-sheet xlsx schema contract — every
`test_excel_schema.py` assertion from the original build still passes
unchanged.

---

## Update: real-world fixes from first live use

The first real run (with actual Gemini/OpenAI keys) surfaced two gaps this
report's original test suite hadn't covered, since they only show up
against a real, occasionally-overloaded API rather than mocked providers:

1. **Gemini's free-tier Flash model returned HTTP 503 ("high demand, try
   again later")** on a normal request. This is Google's own transient
   server-side overload, not a quota issue — but the original code treated
   any non-quota 4xx/5xx as an immediate, non-retried failure for that
   cell. **Fix:** added `request_with_retry()` (`src/providers/base.py`),
   a short exponential-backoff retry (3 attempts, honoring a `Retry-After`
   header when present) wrapped around every LLM provider's HTTP call.
   Transient 503/502/500/504 — and even a brief run of 429s — now get a
   few seconds to clear before the cell is marked failed or the key pool
   is treated as exhausted. Covered by `tests/test_retry_and_key_reset.py`
   (5 tests: succeeds after transient 503, gives up after max retries,
   does not retry non-transient errors like 404, retries 429 before
   escalating, respects `Retry-After`).

2. **A single exhausted key had no way to un-stick within a session.**
   Free-tier rate limits are usually per-minute, but `KeyPool.rotate()`
   permanently marked a key `exhausted` for the rest of the browser
   session — even after the underlying quota recovered — and re-pasting
   the identical key text into the sidebar was a no-op (the UI only
   re-applies a key list when the text actually changes, and the key was
   already present). **Fix:** added `KeyPool.reset_exhausted()` /
   `KeyManager.reset_exhausted()` plus a **"🔄 Reset exhausted keys"**
   button in the sidebar, so a user can explicitly clear exhaustion flags
   after waiting out a transient limit and retry the same keys. Covered by
   4 more tests in `tests/test_retry_and_key_reset.py`, including one that
   documents why the reset button is necessary (identical-text resubmission
   alone does not clear exhaustion).

Both fixes are additive — they change *how many attempts* a call gets
before failing, not the failure-classification contract itself (quota vs.
transient vs. non-retryable errors are still distinguished the same way),
so no prior test needed to change other than one being sped up to mock
`time.sleep` around the new retry delay.

---

```
tests/test_end_to_end.py .......                                       [ 7]
tests/test_excel_schema.py ..........                                  [10]
tests/test_key_rotation.py ........                                    [ 8]
tests/test_resume.py ..                                                [ 2]
tests/test_retry_and_key_reset.py .........                            [ 9]
tests/test_scoring.py ....                                             [ 4]
tests/test_security.py .....                                           [ 5]
tests/test_url_verify.py .........                                     [ 9]
======================== 55 passed in ~1.3s =========================
```

## 1. Unit tests

| Area | File | Status |
|---|---|---|
| URL verification (mocked HTTP: 200/404/timeout) | `test_url_verify.py` | ✅ 9/9 passing |
| Key rotation on quota errors | `test_key_rotation.py` | ✅ 8/8 passing |
| Resume-on-exhaustion logic | `test_resume.py` | ✅ 2/2 passing |
| Scoring formulas (regression vs. sample) | `test_scoring.py` | ✅ 4/4 passing |
| Security (no key leakage) | `test_security.py` | ✅ 5/5 passing |

**Prompt de-duplication/consolidation (Agent 2)** is exercised indirectly
through the end-to-end tests (a run with two near-identical seed prompts
collapses to one Test Set entry) rather than a standalone unit-test file —
`src/agents/agent2_consolidation.py`'s `is_duplicate()`/`dedupe()` functions
are small, pure functions with no external dependencies if you want to add
more direct unit tests later.

## 2. Schema test (`test_excel_schema.py`) — 11/11 passing

Every sheet name and column header the generator produces is compared
against a **second, independent extraction** straight from
`tests/fixtures/sample_reference.xlsx` (not against `src/schema.py`'s own
constants, to avoid the test just checking a copy of itself against
itself). Confirmed exact matches for:

- Sheet order and names (the original 8; a 9th "Executive Summary" sheet was
  added on top in a later update — see the dedicated section near the end
  of this report, and `schema.ORIGINAL_REFERENCE_SHEET_NAMES` vs
  `schema.SHEET_NAMES`)
- `Prompt Discovery`, `LLM Execution`, `GEO Visibility Score` (main table),
  `Citation Intel`, `Content Strategy`, and `Rank Improvement Recs` headers
- `Citation Ranking`'s dynamically-generated per-entity header, reproduced
  exactly for the sample's actual brand + 5 competitor names
- The 4-row metadata block (title + Run ID/Brand/Website + Sector/Region +
  Scope/Generated) present on every sheet
- A generated workbook round-trips through `openpyxl.load_workbook()` cleanly

## 3. End-to-end test with mocked providers (`test_end_to_end.py`) — 7/7 passing

Runs the full pipeline (Agents 1→9, both exports) against fake LLM/search
responses — **zero real API calls** — and confirms:

- A valid, openable `.xlsx` with all required sheets and non-trivial data
  (9 sheets as of the Executive Summary update — see below)
- A valid, browser-openable `.html` (correct `<html>`/`</html>`, inline
  `<script>` only, no external asset references)
- A non-empty content-drafts `.zip`

## 4. Live smoke test — **not run**

Section 7.4 of the build spec calls for "3-5 prompts, Gemini + ChatGPT only,
full pipeline, by hand" against real free-tier keys, if available at build
time. **This build environment has no outbound network access to
`generativelanguage.googleapis.com`, `api.openai.com`, `serpapi.com`, or
`google.serper.dev`** (sandboxed to package registries only), so a live
smoke test could not be executed here. All provider code paths are
otherwise covered by the mocked end-to-end test and by the Streamlit app's
own headless load test (see Section 6).

**Action for you:** run `streamlit run app.py`, paste a real Gemini key (and
optionally an OpenAI key with the opt-in box checked), and start a run with
3-5 seed prompts to get a real wall-clock timing against the PRD's 3-minute
KPI. Given free-tier RPM caps, expect this build's own honest ETA banner
(displayed in the Run tab) to be closer to reality than 3 minutes.

## 5. Forced-exhaustion / resume test (`test_resume.py`) — 2/2 passing

This is the feature the free-tier build most depends on, so it got the most
scrutiny:

- `test_forced_exhaustion_then_resume_no_rework`: seeds a provider whose
  entire key pool (2 keys) is already exhausted, confirms the run pauses
  with status `paused_awaiting_key` and **zero** cells complete, then adds a
  fresh key and re-invokes Agent 3 against the same run — confirms all 5
  cells complete using only the fresh key, with the failed pre-resume calls
  correctly separated from the 5 real post-resume calls (no duplicated
  successful work).
- `test_resume_skips_units_already_done`: confirms a second `run_agent3`
  call against an already-complete run makes **zero** additional provider
  calls.

**A real bug was caught and fixed by this test**: the run status was not
being reset from `paused_awaiting_key` back to `active` when a resume
succeeded. Fixed in `agent3_llm_execution.py` (`run_agent3` now sets the run
active at the start of every invocation, including resumes).

## 6. Edge cases — covered in `test_end_to_end.py`

| Edge case | Test | Result |
|---|---|---|
| Empty competitor list | `test_empty_competitor_list_does_not_crash` | ✅ pass |
| Brand name with special characters (`&`, quotes, em-dash, `%`) | `test_brand_name_with_special_characters` | ✅ pass — survives xlsx round-trip and HTML escaping |
| Zero citations for every model on every prompt | `test_zero_citations_for_every_model_renders_not_cited_not_crash` | ✅ pass — renders the exact "Domains only..." / "No exact page URLs available" pair, never crashes, never leaves a blank cell |
| Malformed/non-JSON LLM response | `test_malformed_llm_response_does_not_crash_run` | ✅ pass — fails that one cell (`failed_other`), run continues; no schema-repair retry was implemented (spec allows "retry... **or** fail that cell cleanly") |
| Non-ASCII location input (Hindi text) | `test_non_ascii_location_input` | ✅ pass — round-trips through xlsx and HTML correctly |
| OpenAI called without opt-in | `test_openai_opt_in_required_before_calling_chatgpt` | ✅ pass — real `OpenAIProvider` raises `OptInRequiredError` before any network call; agent3 catches it, marks that cell `failed_other`, continues |

## 7. Security check — `test_security.py`, 5/5 passing, plus manual sweep

- **No API key appears in a log line or exception message.** Caught and
  fixed a real issue during development: the Gemini provider originally put
  the API key in the URL query string (`?key=...`), which risks the key
  leaking into any exception that echoes the request URL (a documented
  behaviour of some HTTP client errors). Fixed to send the key via the
  `x-goog-api-key` header instead. All four LLM providers additionally pass
  every constructed error message through `redact_secret()` before raising,
  as defense in depth — verified by a test that simulates a connection
  error whose message embeds the key and confirms it never reaches the
  raised exception's text.
- **`.env` is git-ignored.** `.gitignore` includes `.env`, `.env.*` (with an
  explicit `.env.example` exception), plus `runs/`, `*.db`, and generated
  report files.
- **The UI masks pasted keys to their last 4 characters.** `key_manager.mask_key()`
  is used everywhere a key is displayed in the Streamlit sidebar
  (`KeyPool.status_table()`), verified by `test_key_pool_status_table_never_exposes_raw_key`.
- **Manual sweep**: grepped the entire `src/` tree and `app.py` for
  key-shaped strings (`AIzaSy...`, `sk-...`, `sk-ant-...`) — none found.
  `.env.example` contains no real values, comments only.

## 8. Known gaps against the PRD (stated plainly)

- **3-minute-for-30-prompts KPI**: not achievable on free-tier rate limits.
  A realistic run is 8-15 minutes. The app displays this honestly in its
  Run tab rather than a fabricated fast estimate.
- **Citation Intel's exact per-LLM aggregation formula** in the reference
  workbook could not be fully reverse-engineered from the sample alone —
  some values in the sample (e.g. its ChatGPT column reading `0` for a
  domain ChatGPT visibly cited in the same sheet's `LLM Execution` tab)
  imply an internal rule not evidenced anywhere in the 8 exported sheets.
  This build's `agent6_citation_ranking.py` uses a clearly documented,
  defensible alternative formula (domain occurrence in each active LLM's
  Supporting Domains column, plus independent Search Evidence corroboration)
  instead of guessing at the undocumented one. **The GEVS formulas — the
  harder and more consequential half of the scoring logic — *are* regression
  tested byte-for-byte against the sample and match exactly** (Section 5's
  fully worked-out formulas, verified in `test_scoring.py`).
- **Citation Ranking's per-competitor source-URL matching** only works
  reliably for the brand under test (whose website the user provides).
  Competitor-owned domains aren't automatically known from just a company
  name, so competitor citation evidence defaults to "Cited, but no
  entity-specific source URL returned" unless the caller supplies a
  `entity_websites` mapping for competitors too (the plumbing for this
  already exists in `agent6_citation_ranking.py::build_citation_ranking`).
- **Video Intelligence (Agent 4 / YouTube)** is fully implemented
  (`agent4_youtube.py`) but its output is stored only in the run's SQLite
  state, not wired into the xlsx export — the reference workbook has no
  video sheet, so there's no schema contract to write it against yet.
- **Live smoke test not run** (Section 4 above) — no outbound network
  access to any LLM/search API from this build environment.

## Update: the 7-point feature request (human-in-the-loop selection, keyword/semantic density, calibrated drafts, Executive Summary, and the 3 previously-untracked KPIs)

This adds the seven gaps identified against the PRD's Module D and success-metrics
sections. All seven were genuinely zero- or near-zero-cost as scoped (see the
call-count table this was requested against) — no feature here required a new
paid API or a meaningful increase in call volume. 37 new tests, all passing
alongside the existing 99 with no regressions.

1. **Human-in-the-loop winning-link selection.** `pipeline.get_candidate_winning_links()`
   surfaces every citable URL Agent 6 already ranked (domain, times cited,
   source quality, sample triggering prompts) — zero extra calls. `app.py`'s
   Run tab now stops after Agent 6 (Stage ②) and shows this as an editable
   table (`st.data_editor`) with the top `max_winning_links` pre-checked;
   Agents 7-9 only run after the user clicks Stage ④. `run_content_remediation()`
   takes an explicit `selected_links` param with three distinct behaviours,
   covered by `test_content_remediation_selection.py`:
   - `None` → automatic top-N (unchanged default/backward-compatible behaviour).
   - `[...]` → exactly those links, nothing else.
   - `[]` → the user's deliberate choice of *zero* links is honoured (empty
     gap/strategy output), not silently replaced by the old auto-pick fallback.
2. **Keyword/semantic density analysis.** New `src/text_analysis.py` — pure
   stdlib (`collections.Counter`, `re`, `math`), no sklearn/numpy dependency
   added. `top_keywords()` gives ranked frequency + density; `semantic_density_score()`
   is a TF-cosine-similarity proxy between a scraped page and the target
   prompt. **Read the module docstring before calling this a real "semantic"
   score** — it is explicitly *not* an embedding-based semantic similarity
   (that would cost an API call this build avoids); it's a defensible,
   zero-cost approximation for "does this page actually talk about the same
   thing, and how much." 7 tests in `test_text_analysis.py`.
3. **Drafts calibrated to a specific winning link.** `agent8_content_generation.py`
   was rewritten: given Agent 7's analyses, it now generates exactly one
   draft per selected link, using a new `DRAFT_PROMPT_CALIBRATED` template
   built from that link's *condensed* analysis (top 8 keywords, TF-cosine
   topical-alignment score, up to 5 existing headings, structural gaps) —
   never the raw scraped page. This directly addresses the token-budget risk
   flagged at request time: `test_text_analysis.py::test_condensed_analysis_is_short_regardless_of_page_length`
   proves the condensed block's size doesn't scale with a 200x-longer source
   page. A safe fallback (the original prompt-priority selection) is
   preserved for when no winning links are available to calibrate against —
   but only when the caller didn't make an explicit (possibly empty)
   selection; see `allow_fallback` in `run_agent8`.
4. **Executive Summary artifact.** New `src/agents/executive_summary.py`:
   `build_summary_data()` pulls every number from what Agents 5, 6, 7, 9, and
   the new `kpi.py` already computed (zero cost); `render_template_summary()`
   builds a plain-text/markdown narrative from it (zero cost);
   `render_llm_polished_summary()` optionally spends exactly one extra LLM
   call for the *whole run* to rewrite it in natural prose, with an
   always-on fallback to the template on any provider error, missing key, or
   empty response (`test_executive_summary.py`, 9 tests). Surfaced as: a new
   "Executive Summary" sheet (always first in the workbook — see the
   deliberate schema note below), a new top section + 3 KPI cards in the
   HTML report, and a `00_EXECUTIVE_SUMMARY.md` file bundled into the
   drafts zip download.
   - **Deliberate schema change:** `schema.SHEET_NAMES` now has 9 entries
     (Executive Summary + the original 8). This is additive, not a
     replacement — `schema.ORIGINAL_REFERENCE_SHEET_NAMES` preserves the
     original 8 for the fidelity test that compares against the actual
     reference file (`test_sheet_names_match_fixture_exactly`), and a new
     test (`test_generated_workbook_includes_original_eight_plus_executive_summary`)
     asserts the relationship between the two stays additive.
5. **Prompt Relevance KPI.** The PRD's own wording — "% of prompts *deemed
   relevant by the user*" — is a human judgment, not something an LLM should
   self-report. `app.py` now shows the Test Set with a `st.data_editor`
   checkbox column right after Stage ① (prompt discovery), persisted via
   `kpi.record_prompt_relevance()` into the existing SQLite `units` table
   (module=`"prompt_relevance"`) — no new storage mechanism. Zero API calls.
   11 tests in `test_kpi.py` cover recording, re-rating (overwrite not
   duplicate), and aggregation with zero/partial/full rating coverage.
6. **Processing Speed KPI (tracked, not fabricated as achieved).**
   `kpi.start_stage_timer()` / `record_stage_timing()` wrap each pipeline
   stage in `app.py`, persisted the same way as Prompt Relevance. Displayed
   against the PRD's stated 180-second target with an honest "not met" state
   — this build does not pretend free-tier rate limits are solvable by
   better timing code; see Section 8 above.
7. **Citation Accuracy KPI.** `agent3_llm_execution.py`'s execution cell now
   also carries `citation_claimed_count`/`citation_verified_count` (the
   claimed-vs-verified counts `url_verify` already computes on every cell —
   zero new calls, just no longer discarded). `kpi.compute_citation_accuracy()`
   aggregates these into a real percentage against an 80% target, with
   `None` (not a `ZeroDivisionError` or a misleading `0%`) when nothing was
   claimed to verify. 4 dedicated tests plus coverage via the pipeline
   integration test.

### New: an automated UI test (not just pipeline-level tests)

`tests/test_app_smoke.py` uses Streamlit's `AppTest` framework (no browser)
to actually click through `app.py`'s Stage ① and Stage ② buttons with **zero
API keys configured** — the most common real first-time-user scenario — and
asserts no unhandled exception reaches the user. This is the one place in
the suite that exercises `app.py`'s click-handlers directly rather than only
the underlying `pipeline.py` functions, which is exactly where the Stage
①→②→③→④ restructure was riskiest. Stage ③/④ (the link picker and
remediation) are not driven by `AppTest` — they depend on real citation data
that doesn't exist without a configured LLM key, so this is covered instead
by the pipeline-level tests in `test_content_remediation_selection.py`.

### Known limitation carried over from this update

- The Executive Summary's LLM-polish option was tested against a fake
  provider only (`test_executive_summary.py`); it was not exercised with a
  real Groq/Gemini key, same constraint as every other live-API path in this
  build (Section 4 above — no outbound network access from this environment).
