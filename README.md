# GEO Intelligence Suite (Free-Tier Edition)

Measures how often the ChatGPT evaluation slot (Groq-hosted OpenAI GPT-OSS 120B) recommends your brand for a set of
high-intent conversational prompts, which sources those LLMs cite, and what
content to create to close the gap — built to run entirely on free/trial API
tiers. Outputs an `.xlsx` workbook (schema-matched to a reference GEO report)
and a single self-contained `.html` client-shareable report.

Built against `GEO_Intelligence_Suite_Prasad.docx` (Product Brief: Project
Nexus, Phase 2), Sections 1-6, Modules A-D, Agents 1-9.

## What's new in this build

**Latest update** — the 7-point feature/KPI addition (see `TESTING_REPORT.md`
for full detail):

- **Human-in-the-loop winning-link selection** before Agents 7-9 run — a new
  Stage ③ in the Run tab lets you review and pick exactly which cited
  sources get analysed and drafted against, instead of an automatic top-N.
- **Keyword/semantic density analysis** (`src/text_analysis.py`) — pure
  stdlib, zero API cost, feeding Agent 7's page analysis.
- **Drafts calibrated to a specific selected link**, using a condensed
  (not raw) analysis of that link so one draft costs roughly the same in
  tokens regardless of source page length.
- **A new Executive Summary sheet/section/file** — a KPI dashboard +
  narrative, template-built for free with an optional one-call LLM polish.
- **Citation Accuracy, Prompt Relevance, and Processing Speed** are now
  real, tracked numbers (previously undocumented KPIs from the PRD),
  surfaced in the Executive Summary and Results tab.

Found and fixed against a real production run (HDFC Bank / credit cards),
plus several PRD-aligned additions:

- **Fixed a real bug**: any LLM excluded from a run (e.g. running ChatGPT-only)
  used to render as a bare `0` in GEVS/Citation sheets — indistinguishable
  from "tested, zero mentions." Now correctly reads "Not tested" everywhere.
- **Rewrote the Citation Intel source-quality classifier.** It was hardcoded
  to mutual-fund regulator patterns, so every other domain (a bank's own
  site, real editorial authorities like NerdWallet, genuine Reddit/Quora UGC)
  fell into one generic "Weak/supporting evidence" bucket. It now
  distinguishes official-brand, regulatory, editorial-authority, video,
  app-store, and UGC/forum tiers — and separately flags whether a domain was
  **actually cited by a tested LLM** or only found via the independent
  search-evidence pass (an SEO signal, not proof any model trusts it). See
  the new **"Real LLM Citation Rate"** metric in the HTML report.
- **Search-grounded prompting**: a non-browsing model (like gpt-oss via Groq)
  can only "cite" a URL by guessing from training data, which honest
  verification then correctly rejects — meaning every citation reads "No
  exact page URLs available." Agent 3 now feeds the model real, live search
  results to choose from, giving genuine citations an actual chance to appear.
- **Smart RPM+TPM+RPD+TPD rate limiter** (`src/rate_limiter.py`), replacing a
  fixed per-call delay. Tracks each provider+key's real, per-call token usage
  in a rolling window and paces proactively — critical for Groq's tight
  30 RPM / 8,000 TPM combination, where a handful of longer prompts can blow
  the token budget while comfortably under the request budget. Configurable
  live in Advanced settings -> "Rate-limit budgets," with a running usage
  display in the Run tab.
- **Agent 4 (YouTube) now actually runs.** It existed but was never called;
  it now feeds a bonus "Video Intelligence" xlsx sheet (only appended when
  video data exists — the required schema sheets are untouched otherwise)
  and an HTML report section.
- Fixed "Execution Status" in Prompt Discovery being stuck at "Not executed"
  even after a successful run.

## What this build does and doesn't do

| LLM | This build |
|---|---|
| **ChatGPT evaluation slot** | Groq-hosted `openai/gpt-oss-120b`; no direct OpenAI API call. |
| **Gemini** | Optional/disabled by default. |
| **Claude** | Off by default. Anthropic has no ongoing free API tier, only a one-time ~$5 new-account credit. Wire in a key only if you want to spend trial/paid credit — the run degrades this column to "Not tested" automatically once credit runs out. |
| **Perplexity** | Off by default, same reasoning as Claude (small one-time trial credits only). |
| **Copilot** | Not implemented anywhere. Microsoft has no public API for the consumer Copilot chat experience — this reads "Future scope" throughout, matching the reference report. |

Search expansion (Module A) and citation-evidence checks (Module B) use
**SerpApi** (250 free searches/month) or **Serper.dev** (2,500 free searches,
one-time) — never Google's Custom Search JSON API, which is closed to new
signups and being discontinued Jan 1 2027.

**Citation URLs are never presented as fact without verification.** Every
URL an LLM claims as a source is independently checked with an HTTP
HEAD/GET request before being trusted; unverified or unreachable URLs are
labelled "Domains only - no matching page URLs found," never presented as a
real citation.

## Honest runtime expectations

The original PRD's KPI of "<3 minutes for 30 prompts" is **not achievable**
on free-tier rate limits. The app now defaults to one Groq-backed model. Your actual RPM/TPM limits depend on your Groq account/model tier; a 30-prompt run is normally much shorter than the previous two-model free-tier setup. The app's
progress tab shows this estimate up front rather than a falsely fast one —
this is a free-tier rate-limit ceiling, not a bug in the tool.

The default run uses one active model: Groq-hosted OpenAI GPT-OSS 120B in the ChatGPT evaluation slot. Gemini is a real Google API provider and can be enabled in Agent 3 when a Gemini key is configured. Gemini is intentionally not used by Agents 1, 7, or 8 in this build, which keeps Gemini calls limited to the explicit LLM execution/comparison stage.

Module D (content draft generation) is the most token-hungry step. It's
capped by default to reverse-engineering 3 "winning links" and drafting 3
articles per run (both configurable in the Advanced settings panel).

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get your free API keys

**Groq / ChatGPT evaluation slot (required for the default run)**
1. Create an API key in GroqCloud: https://console.groq.com/keys
2. Copy the key.
3. Put it in `GROQ_API_KEYS` or paste it into the app sidebar.
4. The default model is `openai/gpt-oss-120b`; override it with `GROQ_MODEL` if needed.

**OpenAI direct API**
Not used by the default application path. The legacy `src/providers/openai_provider.py` can remain in the repository, but `app.py`, `pipeline.py`, and Agent 3 no longer route ChatGPT requests to OpenAI directly.

**SerpApi or Serper (required for prompt expansion + citation evidence)**
- SerpApi: https://serpapi.com/manage-api-key — 250 free searches/month, no card, recurring.
- Serper.dev: https://serper.dev/api-key — 2,500 free searches, one-time, no card.
- You only need one of these; the app tries SerpApi first, then Serper.

**Claude / Perplexity (optional)** — only worth adding if you have unused
trial credit (Claude: ~$5 one-time on a new Anthropic account; Perplexity:
~$25-50 one-time on a new account). Skip these entirely for a pure
free-tier run.

**YouTube Data API (optional)** — a free Google Cloud API key gives ~100
video searches/day. This feeds Agent 4's video intelligence but isn't part
of the xlsx export yet (the reference report has no video sheet), so it's
safe to skip.

### 3. Configure keys

Either paste comma-separated keys directly into the Streamlit sidebar (they
stay in that browser session only), **or** copy `.env.example` to `.env` and
fill it in:

```bash
cp .env.example .env
```

```dotenv
GEMINI_API_KEYS=AIzaSy...key1,AIzaSy...key2
GROQ_API_KEYS=gsk_...key1
SERPAPI_API_KEYS=your-serpapi-key
```

Comma-separate multiple keys per provider for automatic rotation when one
hits a rate limit.

### 4. Run the app

```bash
streamlit run app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

1. **Setup tab** — enter brand, website, sector, region, competitors
   (comma-separated), and 2-3 seed prompts (one per line).
2. **Run tab**, in order:
   - **① Discover & Review Prompts** — runs Agents 1-2. Once the Test Set is
     finalised, an optional relevance table appears — tick **Relevant?**
     for any prompts you want to rate (tracks the PRD's "% of prompts
     deemed relevant by the user" KPI). This is informational, not a gate:
     you can skip it and move straight on.
   - **② Run Execution & Analytics** — runs Agents 3-6 (and Agent 4/video,
     if a YouTube key is configured). Shows the visibility scorecard once
     done.
   - **③ Select winning links to analyse & outperform** — a table of every
     citable source Agent 6 found, with the top N (by citation count)
     pre-checked. Add or remove any — this is the human-in-the-loop step:
     nothing here costs an extra API call, since it's built entirely from
     data Agent 6 already computed. Unchecking everything is a valid
     choice — Agent 8 will then fall back to drafting against your
     highest-priority absent-brand prompts instead.
   - **④ Analyse Selected Source(s) & Generate Content** — runs Agents 7-9
     against exactly what you picked, generates the Executive Summary, and
     assembles all three downloads.
3. **Results tab** — an Executive Summary section (with Citation Accuracy,
   Prompt Relevance, and Processing Speed KPI cards), the scorecard, and
   downloads: the `.xlsx` workbook (now 9 sheets — see below), the `.html`
   report, and a `.zip` of generated content drafts plus a copy of the
   Executive Summary in markdown.

Every draft in Module D is now calibrated against the *specific* winning
link it's meant to outperform (its keyword profile, topical-alignment
score, and structural gaps — not a generic template), built from a
condensed summary of that page rather than its raw scraped text, so one
draft costs about the same in tokens whether the source page was 500 words
or 5,000.

### 5. Transient errors and rate limits

Two different things can interrupt a run, and they need different fixes:

**Gemini 429/503 and rate limits** — this is
Google's free Flash tier being temporarily overloaded, not your quota. The
app now retries these automatically (a few short backoff attempts) before
giving up on that one cell, so brief spikes usually resolve on their own
without you doing anything.

**"Run paused: a provider's key pool is exhausted"** — this means a real
rate/quota limit was hit on every key you've configured for that provider.
Free-tier limits come in two flavours:

- **Per-minute (RPM)** — the common case with a single Gemini key, since the
  free tier allows only ~10-15 requests/minute. This usually clears within
  a minute. Wait ~60 seconds, click **🔄 Reset exhausted keys** in the
  sidebar, then **② Run Execution & Analytics** again with the same Run ID.
  (Re-pasting the identical key text does *not* reset it — it's already in
  the pool — which is why the explicit reset button exists.)
- **Per-day (RPD) or true quota exhaustion** — wait for the daily reset, or
  get a fresh key for that provider (a new Google/OpenAI account) and paste
  it into the sidebar; it's *added* to that provider's pool, not a
  replacement, so old keys stay in the rotation too.

Either way, re-entering the **same Run ID** and clicking **② Run Execution &
Analytics** again (or **④ Analyse Selected Source(s) & Generate Content**,
if that's the stage that paused) skips every unit of work already
completed — nothing is recomputed, no API calls are wasted, and no results
are lost. This is backed by a SQLite file per run under `runs/<run_id>.db`
that records the status of every unit of work as it completes.

**Tip:** running with multiple comma-separated keys per provider (e.g. from
a couple of Google accounts) means a per-minute limit on one key rotates to
the next automatically, without ever pausing the run.

## Testing

```bash
pytest tests/ -v
```

See `TESTING_REPORT.md` for what was tested, how, and any known gaps.

## Project structure

```
geo-intelligence-suite/
  app.py                      # Streamlit entrypoint
  .env.example                # documented, no real keys
  requirements.txt
  README.md                   # this file
  TESTING_REPORT.md
  src/
    schema.py                  # ground-truth sheet/column schema
    key_manager.py              # key pool rotation + validation
    state_store.py              # SQLite run-state, resume logic
    url_verify.py                # HTTP HEAD/GET citation verification
    text_analysis.py              # keyword frequency + TF-cosine "semantic density"
    kpi.py                          # Citation Accuracy / Prompt Relevance / Processing Speed
    pipeline.py                      # orchestrates Agents 1-9 + human-in-the-loop link selection
    excel_export.py                   # writes the .xlsx
    html_export.py                     # writes the .html report
    providers/                          # one module per LLM + search API
    agents/                              # agent1_prompt_expansion.py ... agent9_summary_export.py,
                                          # executive_summary.py
  tests/
    fixtures/sample_reference.xlsx     # ground-truth reference workbook
    fakes.py                            # shared fake providers, zero real network calls
    test_*.py
```

## Known limitations (stated plainly, not hidden)

- The 3-minute-for-30-prompts KPI from the PRD is not achievable on free
  tiers; see "Honest runtime expectations" above.
- Citation Ranking's per-competitor source-URL matching only works reliably
  for the brand under test (whose website you provide) — competitor-owned
  domains are not automatically known, so competitor citation evidence
  defaults to "Cited, but no entity-specific source URL returned" unless you
  extend `entity_websites` with competitor domains yourself.
- Citation Intel's exact per-LLM citation-count aggregation in the reference
  workbook could not be fully reverse-engineered from the sample alone (see
  `src/agents/agent6_citation_ranking.py` docstring and `TESTING_REPORT.md`)
  — this build uses a clearly documented alternative formula instead of
  guessing at an undocumented one.
- Video Intelligence (Agent 4 / YouTube) is implemented and wired into the
  xlsx export as an optional bonus sheet at the end of the workbook, only
  when a YouTube key is configured — the reference workbook itself has no
  video sheet, so there was no original contract to fit it into.
- "Semantic density" (`src/text_analysis.py`) is a TF-cosine-similarity
  proxy between a scraped page and the target prompt — **not** a true
  embedding-based semantic similarity, which would need an extra API call.
  Read the module docstring before treating this number as more than a
  quota-free approximation.
- The Executive Summary sheet is a genuine addition on top of the PRD's
  original 8-sheet schema (`schema.SHEET_NAMES` now has 9 entries) — this
  is deliberate and tested as additive (see `TESTING_REPORT.md`), not a
  silent schema drift.
- Content Gaps & Recommendations' 6/12-month GEVS growth targets remain
  "Addendum scope - not generated," matching the original reference report's
  own placeholder text — projecting future lift needs a trend history a
  first run doesn't have.
