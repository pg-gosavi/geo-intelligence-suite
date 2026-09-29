"""
GEO Intelligence Suite — Streamlit UI (free-tier build).

Run with:  streamlit run app.py
"""
from __future__ import annotations

import os
import time

import streamlit as st
from dotenv import load_dotenv

from src import key_manager as km
from src import kpi
from src import pipeline
from src import state_store as ss
from src.rate_limiter import RateLimiterRegistry
from src.providers.claude_provider import ClaudeProvider
from src.providers.groq_chatgpt import GroqChatGPTProvider
from src.providers.gemini import GeminiProvider
from src.providers.perplexity import PerplexityProvider
from src.providers.search_serpapi import SerpApiProvider
from src.providers.search_serper import SerperProvider

load_dotenv()

st.set_page_config(page_title="GEO Intelligence Suite", layout="wide")

RUNS_DIR = "runs"
os.makedirs(RUNS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Session state setup
# ---------------------------------------------------------------------------
if "key_manager" not in st.session_state:
    kmgr = km.KeyManager()
    kmgr.load_from_env()
    st.session_state.key_manager = kmgr
if "log_lines" not in st.session_state:
    st.session_state.log_lines = []
if "run_id" not in st.session_state:
    st.session_state.run_id = None
if "run_artifacts" not in st.session_state:
    st.session_state.run_artifacts = {}
if "rate_limiter_registry" not in st.session_state:
    # Persists across reruns/resumes within this browser session, so RPM/TPM
    # sliding windows and RPD/TPD daily counters stay accurate across a
    # paused-then-resumed run instead of resetting every script rerun.
    st.session_state.rate_limiter_registry = RateLimiterRegistry()

key_mgr: km.KeyManager = st.session_state.key_manager


def log(msg: str):
    st.session_state.log_lines.append(msg)


def build_providers():
    return {
        "ChatGPT": GroqChatGPTProvider(),
        "Gemini": GeminiProvider(),
        "Claude": ClaudeProvider(),
        "Perplexity": PerplexityProvider(),
        "search": st.session_state.get("search_provider_obj"),
    }


def sk(name: str, run_id: str):
    """Namespaced session_state key so switching/resuming a Run ID never
    reads stale data left over from a different run."""
    return f"{name}__{run_id}"


# ---------------------------------------------------------------------------
# Sidebar — API keys
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("API Keys")
    st.caption(
        "Paste comma-separated keys for automatic rotation on rate limits. "
        "Keys are masked below and never written to logs or reports."
    )

    def key_input(provider_key, label, help_text):
        existing = ",".join(k.key for k in key_mgr.pool(provider_key).keys)
        value = st.text_area(label, value=existing, height=60, help=help_text, key=f"input_{provider_key}")
        if value != existing:
            key_mgr.set_keys(provider_key, value)
        pool = key_mgr.pool(provider_key)
        if pool.has_keys():
            for row in pool.status_table():
                badge = "🟢" if row["valid"] else ("⚪" if row["valid"] is None else "🔴")
                exhausted = " (exhausted)" if row["exhausted"] else ""
                st.caption(f"{badge} `{row['key']}`{exhausted}")

    key_input(
        "groq",
        "Groq API key(s)",
        "Used for the ChatGPT evaluation slot via OpenAI GPT-OSS 120B on Groq. Get a key from GroqCloud.",
    )
    key_input(
        "gemini",
        "Google Gemini API key(s)",
        "Used for the Gemini evaluation slot via Google's Gemini API. Multiple keys are supported for rotation; quota remains subject to Google's project/tier limits.",
    )
    with st.expander("Optional: Claude, Perplexity (no ongoing free tier — trial credit only)"):
        key_input("claude", "Anthropic (Claude) API key(s)", "One-time ~$5 trial credit on a new account only.")
        key_input("perplexity", "Perplexity (Sonar) API key(s)", "Small one-time trial credit on a new account only.")
    with st.expander("Search API (Module A expansion + citation evidence)"):
        st.caption("Google's Custom Search JSON API is closed to new signups — use SerpApi or Serper instead.")
        key_input("serpapi", "SerpApi key(s)", "250 free searches/month, recurring.")
        key_input("serper", "Serper.dev key(s)", "2,500 free searches, one-time.")
    with st.expander("Optional: YouTube Data API (video intelligence)"):
        key_input("youtube", "YouTube Data API key(s)", "~100 free searches/day.")

    if st.button("Validate all keys"):
        providers_for_validation = {
            "groq": GroqChatGPTProvider(),
            "gemini": GeminiProvider(),
            "claude": ClaudeProvider(), "perplexity": PerplexityProvider(),
        }
        for provider_key, provider_obj in providers_for_validation.items():
            pool = key_mgr.pool(provider_key)
            if pool.has_keys():
                with st.spinner(f"Validating {provider_key} keys..."):
                    pool.validate_all(provider_obj.validate_key)
        st.rerun()

    any_exhausted = any(any(k.exhausted for k in key_mgr.pool(p).keys) for p in km.KeyManager.PROVIDERS)
    if any_exhausted:
        st.warning("One or more keys are marked exhausted from a prior rate-limit hit this session.")
    if st.button("🔄 Reset exhausted keys", disabled=not any_exhausted,
                   help="Free-tier rate limits are often per-minute. If you've waited a bit, "
                        "reset here to retry the SAME keys — re-pasting the identical key text "
                        "does nothing, since it's already in the pool."):
        key_mgr.reset_exhausted()
        st.success("Exhausted flags cleared — click ② Run Execution & Analytics (or the stage that paused) again.")
        st.rerun()

# ---------------------------------------------------------------------------
# Main — run configuration
# ---------------------------------------------------------------------------
st.title("GEO Intelligence Suite")
st.caption("Free-tier build — measures brand visibility across LLM answers and recommends content to close the gap.")

tab_setup, tab_run, tab_results = st.tabs(["1. Setup", "2. Run", "3. Results"])

with tab_setup:
    col1, col2 = st.columns(2)
    with col1:
        brand = st.text_input("Brand", value=st.session_state.get("brand", ""), key="brand_input")
        website = st.text_input("Brand website", value=st.session_state.get("website", ""))
        sector = st.text_input("Sector", value=st.session_state.get("sector", "mutual_funds"))
        region = st.text_input("Region / Location", value=st.session_state.get("region", "India"))
    with col2:
        competitors_raw = st.text_area("Competitors (comma-separated)", value=st.session_state.get("competitors_raw", ""))
        seeds_raw = st.text_area("Seed prompts (one per line, 2-3 recommended)", value=st.session_state.get("seeds_raw", ""), height=100, key="seeds_input")

    with st.expander("Advanced settings"):
        max_test_set = st.number_input("Max Test Set size", min_value=5, max_value=30, value=30)
        active_llms = st.multiselect("Active LLMs (actually called)", ["ChatGPT", "Gemini", "Claude", "Perplexity"],
                                       default=["ChatGPT"])
        max_winning_links = st.number_input("Winning links to reverse-engineer (Module D)", min_value=1, max_value=10, value=3)
        max_drafts = st.number_input("Article drafts to generate (Module D)", min_value=1, max_value=10, value=3)
        polish_summary_with_llm = st.checkbox(
            "Polish the Executive Summary with one extra LLM call",
            value=False,
            help="Costs exactly one extra call for the whole run. Falls back to the plain "
                 "template automatically if that call fails or no key is configured.",
        )
        groq_rate_limit_delay = st.number_input(
            "Groq / GPT-OSS minimum delay (seconds)", min_value=0.0, value=float(os.getenv("GROQ_MIN_INTERVAL_S", "0.5")), step=0.1,
            help="Extra fixed floor on top of the smart RPM/TPM pacer below — useful only if you share this key with other usage outside this app."
        )
        gemini_rate_limit_delay = st.number_input(
            "Gemini minimum delay (seconds)", min_value=0.0, value=float(os.getenv("GEMINI_MIN_INTERVAL_S", "6.5")), step=0.5,
            help="Extra fixed floor on top of the smart RPM/TPM pacer below."
        )

        st.caption(
            "Rate-limit budgets — the app paces calls to stay under these automatically, tracking real "
            "token usage per request rather than guessing. Set these to your actual dashboard numbers."
        )
        rl_col1, rl_col2 = st.columns(2)
        with rl_col1:
            st.markdown("**Groq (openai/gpt-oss-120b)**")
            groq_rpm = st.number_input("Requests/minute", min_value=1, value=pipeline.DEFAULT_PROVIDER_LIMITS["ChatGPT"]["rpm"], key="groq_rpm")
            groq_tpm = st.number_input("Tokens/minute", min_value=100, value=pipeline.DEFAULT_PROVIDER_LIMITS["ChatGPT"]["tpm"], step=100, key="groq_tpm")
            groq_rpd = st.number_input("Requests/day", min_value=1, value=pipeline.DEFAULT_PROVIDER_LIMITS["ChatGPT"]["rpd"], key="groq_rpd")
            groq_tpd = st.number_input("Tokens/day", min_value=100, value=pipeline.DEFAULT_PROVIDER_LIMITS["ChatGPT"]["tpd"], step=1000, key="groq_tpd")
        with rl_col2:
            st.markdown("**Gemini** ([check your actual limits](https://ai.google.dev/gemini-api/docs/rate-limits))")
            gemini_rpm = st.number_input("Requests/minute", min_value=1, value=pipeline.DEFAULT_PROVIDER_LIMITS["Gemini"]["rpm"], key="gemini_rpm")
            gemini_tpm = st.number_input("Tokens/minute", min_value=100, value=pipeline.DEFAULT_PROVIDER_LIMITS["Gemini"]["tpm"], step=1000, key="gemini_tpm")
            gemini_rpd = st.number_input("Requests/day", min_value=1, value=pipeline.DEFAULT_PROVIDER_LIMITS["Gemini"]["rpd"], key="gemini_rpd")

    st.session_state.update(dict(
        brand=brand, website=website, sector=sector, region=region,
        competitors_raw=competitors_raw, seeds_raw=seeds_raw,
    ))

with tab_run:
    est_prompts = max_test_set
    est_models = max(len(active_llms), 1)
    per_prompt_delay = (groq_rate_limit_delay if "ChatGPT" in active_llms else 0.0) + (gemini_rate_limit_delay if "Gemini" in active_llms else 0.0)
    est_seconds = est_prompts * (1.0 + per_prompt_delay)
    st.info(
        f"ETA: ~{est_prompts} prompts × {est_models} active model(s). "
        f"Groq and Gemini are paced independently; Gemini is the slower leg when enabled. "
        f"Estimated with configured pacing: ~{int(est_seconds // 60)} min {int(est_seconds % 60)} sec (rough, network-dependent). "
        f"PRD target is <3 min — free-tier rate limits usually make that unreachable regardless of pacing; "
        f"see README."
    )

    run_id_input = st.text_input("Run ID (blank = new run)", value=st.session_state.run_id or "")
    colA, colB, colC = st.columns(3)
    start_clicked = colA.button("① Discover & Review Prompts", type="primary")
    reset_clicked = colB.button("Reset Run ID")
    colC.write("")

    if reset_clicked:
        st.session_state.run_id = None
        st.rerun()

    log_area = st.empty()

    def ui_log(msg):
        log(msg)
        log_area.code("\n".join(st.session_state.log_lines[-30:]))

    # ----- Stage 1: Prompt Discovery + Consolidation, then relevance rating
    if start_clicked:
        if not brand or not seeds_raw.strip():
            st.error("Brand and at least one seed prompt are required.")
        else:
            run_id = run_id_input.strip() or f"{int(time.time())}-{brand.lower().replace(' ', '-')[:20]}"
            st.session_state.run_id = run_id
            config = {
                "brand": brand, "website": website, "sector": sector, "region": region,
                "competitors": [c.strip() for c in competitors_raw.split(",") if c.strip()],
                "seed_prompts": [s.strip() for s in seeds_raw.splitlines() if s.strip()],
                "active_llms": active_llms,
                "max_test_set": int(max_test_set), "max_winning_links": int(max_winning_links),
                "max_drafts": int(max_drafts),
                "groq_rate_limit_delay_s": float(groq_rate_limit_delay),
                "gemini_rate_limit_delay_s": float(gemini_rate_limit_delay),
                "provider_limits": {
                    "ChatGPT": {"rpm": int(groq_rpm), "tpm": int(groq_tpm), "rpd": int(groq_rpd), "tpd": int(groq_tpd)},
                    "Gemini": {"rpm": int(gemini_rpm), "tpm": int(gemini_tpm), "rpd": int(gemini_rpd)},
                },
            }

            store = ss.StateStore(ss.db_path_for_run(run_id, base_dir=RUNS_DIR))
            existing_run = store.get_run(run_id)
            if existing_run is None:
                store.create_run(run_id, config)
            else:
                config = existing_run["config"]  # resume with the original config
            st.session_state[sk("config", run_id)] = config

            st.session_state.search_provider_obj = (
                SerpApiProvider() if key_mgr.any_configured("serpapi") else
                (SerperProvider() if key_mgr.any_configured("serper") else None)
            )
            providers = build_providers()

            with st.spinner("Running Agents 1-2: Prompt Discovery & Consolidation..."):
                started_at = kpi.start_stage_timer()
                pd_rows = pipeline.run_prompt_discovery(store, run_id, config, providers, key_mgr, log=ui_log)
                kpi.record_stage_timing(store, run_id, "agent1_2_prompt_discovery", started_at)
            st.session_state[sk("pd_rows", run_id)] = pd_rows
            # A fresh discovery invalidates any later-stage results from a
            # previous attempt at this same Run ID.
            for key in ("exec_rows", "run_status", "gevs", "citrank", "citintel",
                         "video_results", "candidate_links", "remediation_done"):
                st.session_state.pop(sk(key, run_id), None)
            store.close()

    run_id = st.session_state.run_id
    pd_rows = st.session_state.get(sk("pd_rows", run_id)) if run_id else None

    # ----- Prompt Relevance rating (KPI #5) — shown once prompts exist -----
    if run_id and pd_rows:
        test_set = [r for r in pd_rows if r["include_in_test_set"] == "Yes"]
        st.success(f"Test Set finalised: {len(test_set)} prompts.")

        with st.expander(f"Rate prompt relevance ({len(test_set)} prompts) — optional, tracks the PRD's "
                           f"'% of prompts deemed relevant by the user' KPI", expanded=True):
            store = ss.StateStore(ss.db_path_for_run(run_id, base_dir=RUNS_DIR))
            existing_ratings = kpi.get_prompt_relevance_ratings(store, run_id)
            table_rows = [
                {"num": r["num"], "Prompt": r["conversational_prompt"],
                 "Relevant?": existing_ratings.get(r["num"])}
                for r in test_set
            ]
            edited = st.data_editor(
                table_rows, hide_index=True, key=f"relevance_editor_{run_id}",
                column_config={
                    "num": st.column_config.NumberColumn("#", disabled=True),
                    "Prompt": st.column_config.TextColumn("Prompt", disabled=True, width="large"),
                    "Relevant?": st.column_config.CheckboxColumn("Relevant?"),
                },
            )
            if st.button("Save relevance ratings", key=f"save_relevance_{run_id}"):
                for row in edited:
                    if row["Relevant?"] is not None:
                        kpi.record_prompt_relevance(store, run_id, row["num"], bool(row["Relevant?"]))
                st.success("Ratings saved.")
                st.rerun()
            summary = kpi.compute_prompt_relevance(store, run_id, test_set)
            if summary["rated_count"]:
                st.caption(f"{summary['relevance_pct']}% relevant so far ({summary['rated_count']}/{summary['total_prompts']} rated).")
            store.close()

        # ----- Stage 2: Execution + Analytics -----
        st.divider()
        run_execution_clicked = st.button("② Run Execution & Analytics (Agents 3-6)", type="primary", key=f"run_exec_{run_id}")

        if run_execution_clicked:
            config = st.session_state[sk("config", run_id)]
            st.session_state.search_provider_obj = st.session_state.get("search_provider_obj") or (
                SerpApiProvider() if key_mgr.any_configured("serpapi") else
                (SerperProvider() if key_mgr.any_configured("serper") else None)
            )
            providers = build_providers()
            store = ss.StateStore(ss.db_path_for_run(run_id, base_dir=RUNS_DIR))

            started_at = kpi.start_stage_timer()
            progress_bar = st.progress(0.0, text="Running Agent 3: LLM Execution...")
            exec_rows, run_status = pipeline.run_execution(
                store, run_id, config, providers, key_mgr, test_set, log=ui_log,
                rate_limiter_registry=st.session_state.rate_limiter_registry,
            )
            progress_bar.progress(1.0 if run_status != ss.RUN_PAUSED else 0.5,
                                    text=f"Agent 3 status: {run_status}")
            st.session_state[sk("exec_rows", run_id)] = exec_rows
            st.session_state[sk("run_status", run_id)] = run_status

            if run_status != ss.RUN_PAUSED:
                video_results = {}
                if key_mgr.any_configured("youtube"):
                    with st.spinner("Running Agent 4: Video Intelligence (YouTube)..."):
                        video_results = pipeline.run_video_intelligence(
                            store, run_id, config, key_mgr, test_set, log=ui_log,
                        )
                with st.spinner("Running Agents 5-6: Visibility Score & Citation Analysis..."):
                    gevs, citrank, citintel = pipeline.run_analytics(config, exec_rows, log=ui_log)
                kpi.record_stage_timing(store, run_id, "agent3_6_execution_and_analytics", started_at)

                st.session_state[sk("gevs", run_id)] = gevs
                st.session_state[sk("citrank", run_id)] = citrank
                st.session_state[sk("citintel", run_id)] = citintel
                st.session_state[sk("video_results", run_id)] = video_results
                st.session_state[sk("candidate_links", run_id)] = pipeline.get_candidate_winning_links(
                    exec_rows, citrank, citintel, config, max_candidates=10,
                )
                st.session_state.pop(sk("remediation_done", run_id), None)
            store.close()

        run_status = st.session_state.get(sk("run_status", run_id))
        if run_status == ss.RUN_PAUSED:
            with st.expander("Rate-limit budget usage this session", expanded=True):
                snapshot = st.session_state.rate_limiter_registry.snapshot()
                if not snapshot:
                    st.caption("No provider calls made yet.")
                for key, usage in snapshot.items():
                    st.markdown(f"**{key}**")
                    cols = st.columns(4)
                    cols[0].metric("Req/min", f"{usage['requests_this_minute']}/{usage['rpm_limit'] or '—'}")
                    cols[1].metric("Tok/min", f"{usage['tokens_this_minute']}/{usage['tpm_limit'] or '—'}")
                    cols[2].metric("Req/day", f"{usage['requests_today']}/{usage['rpd_limit'] or '—'}")
                    cols[3].metric("Tok/day", f"{usage['tokens_today']}/{usage['tpd_limit'] or '—'}")
            st.warning(
                "Run paused: a provider's key pool is exhausted. For Gemini 429s, wait for Google's rate-limit window to recover and "
                "click **🔄 Reset exhausted keys** in the sidebar, then **② Run Execution & Analytics** "
                "again with the same Run ID. If it's a genuine daily quota limit, paste a fresh "
                "key instead. Either way, no completed work will be repeated."
            )

        # ----- Human-in-the-loop winning-link selection + Stage 3 -----
        candidate_links = st.session_state.get(sk("candidate_links", run_id))
        gevs = st.session_state.get(sk("gevs", run_id))
        if gevs and candidate_links is not None:
            st.divider()
            st.subheader("Scorecard")
            cols = st.columns(4)
            brand_stats = next(s for s in gevs["main_table"] if s["entity"] == gevs["brand"])
            leader = gevs["main_table"][0]
            citintel = st.session_state.get(sk("citintel", run_id)) or []
            gap_result_preview = st.session_state.get(sk("gap_result", run_id))
            cols[0].metric(f"{gevs['brand']} GEVS (Top-3)", f"{brand_stats['gevs_top3']}%")
            cols[1].metric("Category Leader", leader["entity"], f"{leader['gevs_top3']}%")
            top_domain = citintel[0]["domain"] if citintel else "—"
            cols[2].metric("Top Referenced Domain", top_domain)
            cols[3].metric("Candidate Winning Links", len(candidate_links))

            st.subheader("③ Select winning links to analyse & outperform")
            st.caption(
                "Module D's human-in-the-loop step: pick which cited sources actually get scraped "
                "and drafted against in Agents 7-8 — nothing here costs an extra API call, it's the "
                "citation data Agent 6 already computed. The top "
                f"{st.session_state[sk('config', run_id)]['max_winning_links']} links are pre-selected, but you can uncheck any of them or check additional ones."
            )
            max_pick = st.session_state[sk("config", run_id)]["max_winning_links"]
            default_selected = {c["url"] for c in candidate_links[:max_pick]}
            link_table_rows = [
                {
                    "Select": c["url"] in default_selected,
                    "Domain": c["domain"], "URL": c["url"],
                    "Times Cited": c["total_citations_for_domain"],
                    "Source Quality": c["source_quality"],
                    "Sample Prompt": (c["sample_prompts"][0] if c["sample_prompts"] else ""),
                }
                for c in candidate_links
            ]
            edited_links = st.data_editor(
                link_table_rows, hide_index=True, key=f"link_picker_{run_id}",
                column_config={
                    "Select": st.column_config.CheckboxColumn("Select"),
                    "URL": st.column_config.TextColumn("URL", width="large"),
                    "Sample Prompt": st.column_config.TextColumn("Sample Prompt", width="large"),
                },
            ) if link_table_rows else []
            selected_links = [row["URL"] for row in edited_links if row["Select"]]
            if not link_table_rows:
                st.info("No verified citation URLs were found this run — Agent 8 will fall back to "
                         "drafting against the highest-priority absent-brand prompts instead.")

            remediate_clicked = st.button(
                f"④ Analyse {len(selected_links)} Selected Source(s) & Generate Content (Agents 7-9)",
                type="primary", key=f"remediate_{run_id}", disabled=not link_table_rows and False,
            )

            if remediate_clicked:
                config = st.session_state[sk("config", run_id)]
                exec_rows = st.session_state[sk("exec_rows", run_id)]
                citrank = st.session_state[sk("citrank", run_id)]
                citintel = st.session_state[sk("citintel", run_id)]
                video_results = st.session_state.get(sk("video_results", run_id)) or {}
                store = ss.StateStore(ss.db_path_for_run(run_id, base_dir=RUNS_DIR))
                providers = build_providers()

                remediation_started_at = kpi.start_stage_timer()
                with st.spinner("Running Agents 7-9: Source Analysis, Content Strategy, Export..."):
                    # link_table_rows non-empty but zero rows checked -> the
                    # user deliberately chose zero links; [] (not None) is
                    # passed straight through so that choice is honoured
                    # rather than silently replaced by the auto top-N pick.
                    gap_result, draft_result = pipeline.run_content_remediation(
                        config, exec_rows, citrank, providers, key_mgr,
                        selected_links=selected_links if link_table_rows else None,
                        log=ui_log,
                    )
                    targets = pipeline.build_content_gaps_targets(gevs, citrank, gap_result, config["brand"])
                    rank_recs = pipeline.run_summary(config, exec_rows)
                    kpi.record_stage_timing(store, run_id, "agent7_9_remediation", remediation_started_at)

                    summary_data, kpi_data = pipeline.run_executive_summary(
                        store, run_id, config, test_set, exec_rows, gevs, citintel, gap_result, targets,
                        draft_result, providers=providers, key_manager=key_mgr,
                        polish_with_llm=polish_summary_with_llm, log=ui_log,
                    )

                    wb, cfg = pipeline.assemble_workbook(
                        config, pd_rows, exec_rows, gevs, citrank, citintel,
                        gap_result, targets, draft_result, rank_recs, run_id,
                        video_results=video_results, executive_summary_data=summary_data,
                    )
                    html_report = pipeline.assemble_html(
                        cfg, gevs, citintel, gap_result, rank_recs,
                        execution_rows=exec_rows, video_results=video_results,
                        executive_summary_markdown=summary_data["narrative_markdown"], kpi_data=kpi_data,
                    )
                    drafts_zip = pipeline.assemble_drafts_zip(
                        draft_result, extra_files={"00_EXECUTIVE_SUMMARY.md": summary_data["narrative_markdown"]}
                    )
                    store.set_run_status(run_id, ss.RUN_COMPLETE)

                xlsx_path = os.path.join(RUNS_DIR, f"{run_id}-geo-report.xlsx")
                wb.save(xlsx_path)
                st.session_state.run_artifacts = {
                    "xlsx_path": xlsx_path, "html": html_report, "drafts_zip": drafts_zip,
                    "gevs": gevs, "citintel": citintel, "gap_result": gap_result,
                    "summary_data": summary_data, "kpi_data": kpi_data,
                }
                st.session_state[sk("remediation_done", run_id)] = True
                store.close()
                st.success("Run complete — see the Results tab for downloads.")

with tab_results:
    artifacts = st.session_state.run_artifacts
    if not artifacts:
        st.info("No completed run yet. Configure and start a run in the previous tabs.")
    else:
        st.subheader("Executive Summary")
        kd = artifacts["kpi_data"]
        cols = st.columns(3)
        ca_val = f"{kd['citation_accuracy']['accuracy_pct']}%" if kd["citation_accuracy"]["accuracy_pct"] is not None else "—"
        pr_val = f"{kd['prompt_relevance']['relevance_pct']}%" if kd["prompt_relevance"]["relevance_pct"] is not None else "not rated"
        ps_val = kpi.format_seconds(kd["processing_speed"]["total_elapsed_s"])
        cols[0].metric("Citation Accuracy", ca_val, help=f"Target: {kd['citation_accuracy']['target_pct']}%")
        cols[1].metric("Prompt Relevance", pr_val,
                          help=f"{kd['prompt_relevance']['rated_count']}/{kd['prompt_relevance']['total_prompts']} prompts rated")
        cols[2].metric("Processing Speed", ps_val, help="PRD target: <3 min — usually unreachable on free tiers, see README")
        st.markdown(artifacts["summary_data"]["narrative_markdown"])

        st.divider()
        st.subheader("Scorecard")
        gevs = artifacts["gevs"]
        cols = st.columns(4)
        brand_stats = next(s for s in gevs["main_table"] if s["entity"] == gevs["brand"])
        leader = gevs["main_table"][0]
        cols[0].metric(f"{gevs['brand']} GEVS (Top-3)", f"{brand_stats['gevs_top3']}%")
        cols[1].metric("Category Leader", leader["entity"], f"{leader['gevs_top3']}%")
        top_domain = artifacts["citintel"][0]["domain"] if artifacts["citintel"] else "—"
        cols[2].metric("Top Referenced Domain", top_domain)
        cols[3].metric("Priority Content Gaps", len(artifacts["gap_result"]["gap_rows"]))

        st.divider()
        st.subheader("Downloads")
        with open(artifacts["xlsx_path"], "rb") as f:
            st.download_button("⬇ Download .xlsx workbook", f, file_name=os.path.basename(artifacts["xlsx_path"]))
        st.download_button("⬇ Download .html report", artifacts["html"], file_name="geo-report.html", mime="text/html")
        st.download_button("⬇ Download content drafts + summary (.zip)", artifacts["drafts_zip"], file_name="content-drafts.zip")

        with st.expander("Preview HTML report"):
            st.components.v1.html(artifacts["html"], height=800, scrolling=True)
