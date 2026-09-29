"""
Agent 6 — Citation Analysis & Ranked Source List (Module C).

Builds two sheets:

  - "Citation Ranking": per-prompt, per-LLM, per-entity brand-position table
    plus each cell's source-URL evidence, and an "Avg Brand Rank" column
    for the benchmark brand across whichever active LLMs cited it.

  - "Citation Intel": the aggregated "Most Referenced Links" table — which
    domains show up as evidence for this topic, source-quality tagging, and
    a priority action per domain.

A note on exactness: the GEVS formulas in agent5 were fully reverse-engineered
and are regression-tested byte-for-byte against the sample fixture. Citation
Intel's exact per-LLM citation-count aggregation in the reference workbook
could not be fully reverse-engineered from the sample alone (some values,
e.g. its ChatGPT column reading 0 for domains ChatGPT visibly cited in "LLM
Execution", imply an internal verification/aggregation rule not evidenced in
the exported columns). This module therefore uses a clearly-documented,
defensible formula instead of attempting to byte-match that sheet — see
TESTING_REPORT.md for what was and wasn't verified against ground truth.

A second, important note on what "Source Quality" actually measures: a
domain can land in Citation Intel two different ways —

  1. An active LLM's own answer text named that domain (real GEO signal:
     the model chose to cite/mention it).
  2. The domain only ever showed up in the independent Search Evidence pass
     (SerpApi/Serper's organic results for the same query) — real SEO
     signal about what already ranks on Google, but NOT evidence that any
     tested LLM trusts or cites it.

Many non-grounded/open-weight models (anything without live browsing) will
*never* produce a real cited URL — every "Cited URLs" cell for them reads
"No exact page URLs available" — in which case 100% of a run's Citation
Intel table is really reporting on Google's organic results, not on LLM
citation behaviour at all. This module tracks that provenance per domain
(`llm_cited` vs. `search_evidence_only`) and surfaces it plainly in the
Priority Action text, rather than presenting search-evidence-only domains
as if an LLM had actually cited them.
"""
from __future__ import annotations

from ..schema import (
    ALL_LLMS,
    CITATION_STATUS_CITED_NO_URL,
    CITATION_STATUS_NOT_CITED_PROMPT,
    CITATION_STATUS_NOT_CITED_RANK,
    PROVIDER_DEFAULT_STATUS,
    SOURCE_QUALITY_APP_DISTRIBUTION,
    SOURCE_QUALITY_EDITORIAL_AUTHORITY,
    SOURCE_QUALITY_OFFICIAL_BRAND,
    SOURCE_QUALITY_REGULATORY,
    SOURCE_QUALITY_UNCLASSIFIED,
    SOURCE_QUALITY_VIDEO,
    SOURCE_QUALITY_WEAK_SUPPORTING,
)

# --- Source-quality hint lists ----------------------------------------------
# These are starter heuristics, not an exhaustive or authoritative registry.
# Extend them for your own vertical/region — e.g. add IRDAI/insurance
# regulators, or your market's dominant comparison sites — rather than
# treating this as complete. Anything not matched falls into
# SOURCE_QUALITY_UNCLASSIFIED (an honest "not yet categorized," not a
# confident "this is weak evidence").

REGULATORY_HINTS = (
    ".gov.in", ".gov.uk", ".gov", "sebi.gov.in", "amfiindia.com", "rbi.org.in", "irdai.gov.in",
    "incometax.gov.in", "sec.gov", "consumerfinance.gov", "federalreserve.gov", "fca.org.uk",
    "bankofengland.co.uk", "europa.eu",
)

# Third-party editorial / comparison-authority domains widely cited for
# financial and consumer-product research. Genuinely extensible — add your
# sector's trade press and comparison authorities here.
EDITORIAL_AUTHORITY_HINTS = (
    # Finance — India
    "moneycontrol.com", "valueresearchonline.com", "paisabazaar.com", "economictimes.indiatimes.com",
    "livemint.com", "business-standard.com", "cleartax.in", "groww.in", "zerodha.com",
    # Finance — global
    "nerdwallet.com", "bankrate.com", "investopedia.com", "creditkarma.com", "forbes.com",
    "wsj.com", "ft.com", "reuters.com", "bloomberg.com", "morningstar.com",
    # General consumer/tech editorial
    "cnet.com", "techcrunch.com", "pcmag.com", "wirecutter.com", "tomsguide.com",
    ".edu",
)

UGC_FORUM_HINTS = (
    "reddit.com", "quora.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "tiktok.com", "threads.net", "discord.com", "forum.", "community.",
)

VIDEO_HINTS = ("youtube.com", "youtu.be", "vimeo.com")
APP_STORE_HINTS = ("play.google.com", "apps.apple.com")


def _entity_domain_matches(entity: str, entity_website: str, cited_domains_csv: str) -> bool:
    if not entity_website or not cited_domains_csv:
        return False
    domain = entity_website.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    return domain.lower() in cited_domains_csv.lower()


def _default_for_inactive(llm: str) -> str:
    """Not-active LLMs always render a proper placeholder ("Not tested" /
    "Future scope") — never a numeric 0 or a bare '-', which would read as
    "tested and absent" rather than "never run."""
    return PROVIDER_DEFAULT_STATUS.get(llm) or "Not tested"


def build_citation_ranking(execution_rows: list, brand: str, competitors: list,
                             active_llms: list, entity_websites: dict = None) -> list:
    entities = [brand] + list(competitors)
    entity_websites = entity_websites or {}
    out_rows = []

    for row in execution_rows:
        out = {"num": row["num"], "conversational_prompt": row["conversational_prompt"],
               "intent_category": row["intent_category"], "per_llm_entity": {}}

        brand_ranks_this_prompt = []
        for llm in ALL_LLMS:  # every LLM the schema tracks, not just a hardcoded subset
            if llm not in active_llms:
                default = _default_for_inactive(llm)
                for entity in entities:
                    out["per_llm_entity"][(llm, entity)] = (default, default)
                continue

            cell = row["per_llm"].get(llm, {})
            for entity in entities:
                pos = cell.get("position", {}).get(entity, "-")
                if pos == "-" or not cell.get("mentioned", {}).get(entity):
                    out["per_llm_entity"][(llm, entity)] = ("-", CITATION_STATUS_NOT_CITED_PROMPT)
                    continue
                if entity == brand:
                    brand_ranks_this_prompt.append(int(pos.lstrip("#")))
                domains_csv = cell.get("domains", "") or ""
                if _entity_domain_matches(entity, entity_websites.get(entity, ""), domains_csv):
                    source_urls = cell.get("cited_urls", "") or CITATION_STATUS_CITED_NO_URL
                else:
                    source_urls = CITATION_STATUS_CITED_NO_URL
                out["per_llm_entity"][(llm, entity)] = (pos, source_urls)

        out["avg_brand_rank"] = (
            round(sum(brand_ranks_this_prompt) / len(brand_ranks_this_prompt), 1)
            if brand_ranks_this_prompt else CITATION_STATUS_NOT_CITED_RANK
        )
        out_rows.append(out)

    return out_rows


def _classify_source_quality(domain: str, entity_domains: set) -> str:
    d = domain.lower()
    if any(d == ed or d.endswith("." + ed) for ed in entity_domains):
        return SOURCE_QUALITY_OFFICIAL_BRAND
    if any(h in d for h in REGULATORY_HINTS):
        return SOURCE_QUALITY_REGULATORY
    if any(h in d for h in APP_STORE_HINTS):
        return SOURCE_QUALITY_APP_DISTRIBUTION
    if any(h in d for h in VIDEO_HINTS):
        return SOURCE_QUALITY_VIDEO
    if any(h in d for h in UGC_FORUM_HINTS):
        return SOURCE_QUALITY_WEAK_SUPPORTING
    if any(h in d for h in EDITORIAL_AUTHORITY_HINTS):
        return SOURCE_QUALITY_EDITORIAL_AUTHORITY
    return SOURCE_QUALITY_UNCLASSIFIED


def _priority_action(domain: str, quality: str, brand: str, llm_cited: bool, active_llms: list) -> str:
    provenance = (
        "Actually cited by a tested LLM — a real GEO signal."
        if llm_cited else
        f"Found only via independent search evidence (Google's organic results for this query), "
        f"NOT cited by any tested LLM ({', '.join(active_llms) or 'none active'}) in this run — an SEO "
        f"signal, not proof any model trusts this source."
    )
    if quality == SOURCE_QUALITY_OFFICIAL_BRAND:
        base = f"{domain} is an official brand-owned source — a strong trust signal if kept current and citable."
    elif quality == SOURCE_QUALITY_REGULATORY:
        base = f"{domain} is a regulator/government source. Check whether {brand} content aligns with and links to it — high-trust anchor for compliance-sensitive claims."
    elif quality == SOURCE_QUALITY_APP_DISTRIBUTION:
        base = f"{domain} is an app marketplace; refresh {brand}'s listing with current screenshots, ratings response, and FAQ matching common LLM-asked questions."
    elif quality == SOURCE_QUALITY_EDITORIAL_AUTHORITY:
        base = f"{domain} is a recognized editorial/comparison authority. Review whether {brand} is covered there, and pitch/update coverage to close the gap."
    elif quality == SOURCE_QUALITY_VIDEO:
        base = f"{domain} is a video platform — check which channels rank for these queries and whether {brand} has a presence or partnership angle."
    elif quality == SOURCE_QUALITY_WEAK_SUPPORTING:
        base = f"{domain} is community/social content (forum, UGC) — genuinely weak as standalone evidence; useful only as a directional signal of public sentiment, never as primary recommendation evidence."
    else:
        base = f"{domain} isn't in this build's classification lists yet — reviewed manually, not auto-scored as weak or strong."
    return f"{base} {provenance}"


def build_citation_intel(execution_rows: list, brand: str, active_llms: list, entity_websites: dict = None) -> list:
    entity_domains = set()
    for site in (entity_websites or {}).values():
        d = (site or "").replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
        if d:
            entity_domains.add(d.lower())

    domain_stats: dict = {}

    def bump(domain, llm, by=1):
        if not domain:
            return
        rec = domain_stats.setdefault(domain, {llm_key: 0 for llm_key in
                                                ("ChatGPT", "Gemini", "Claude", "Perplexity", "Copilot", "search")})
        rec[llm] = rec.get(llm, 0) + by

    for row in execution_rows:
        for llm in active_llms:
            cell = row["per_llm"].get(llm, {})
            domains_csv = cell.get("domains") or ""
            for d in [x.strip() for x in domains_csv.split(",") if x.strip()]:
                bump(d, llm)
        for d in row.get("search_evidence_domains", []):
            bump(d, "search")

    ranked = []
    for domain, counts in domain_stats.items():
        per_llm_counts = {}
        for llm in ALL_LLMS:
            if llm in active_llms:
                per_llm_counts[llm] = counts.get(llm, 0)
            else:
                per_llm_counts[llm] = _default_for_inactive(llm)

        llm_cited = any(isinstance(v, int) and v > 0 for v in per_llm_counts.values())
        numeric_total = sum(v for v in per_llm_counts.values() if isinstance(v, int)) + counts.get("search", 0)
        quality = _classify_source_quality(domain, entity_domains)
        ranked.append({
            "domain": domain,
            "source_quality": quality,
            "total_citations": numeric_total,
            "per_llm": per_llm_counts,
            "llm_cited": llm_cited,
            "priority_action": _priority_action(domain, quality, brand, llm_cited, active_llms),
        })

    ranked.sort(key=lambda r: r["total_citations"], reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i
    return ranked
