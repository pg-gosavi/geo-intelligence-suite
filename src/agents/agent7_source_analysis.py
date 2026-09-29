"""
Agent 7 — Reverse Engineering of winning links (Module D).

Scrapes a capped number of "winning links" (the highest-value cited URLs
Agent 6 surfaced — capped at `max_links`, default 3, configurable, per the
PRD's own user-flow example of "System recommends 3 articles to write") and
checks each for the structural/E-E-A-T signals that make a page LLM-citable.

Produces the row data for the "Content Gaps & Recommendations" sheet: one
row per gap *type* found on any analysed link, with triggering prompts and
evidence URLs merged across every link that exhibited it — matching the
sample workbook's own layout.
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from ..providers.base import ProviderError, QuotaExceededError
from .. import text_analysis

TIMEOUT_S = 15
USER_AGENT = "GEOIntelligenceSuite/1.0 (+content-gap-analysis)"

GAP_TYPES = [
    ("brand_missing", "Brand not mentioned on analysed page", "Brand/entity coverage update", "🔴 High", "0-30 days", "content_benchmark"),
    ("no_question_headings", "Lack of question-based H2/H3 headings", "Heading structure update", "🔴 High", "0-30 days", "content_benchmark"),
    ("no_direct_answer_intro", "Missing direct-answer introduction (0-150 words)", "Answer-first page section", "🔴 High", "0-30 days", "weak_context"),
    ("no_faq", "No FAQ section on core category/service pages", "FAQ block", "🔴 High", "0-30 days", "content_benchmark"),
    ("weak_eeat", "Weak E-E-A-T: no author attribution or expert review", "E-E-A-T enhancement", "🔴 High", "0-30 days", "content_benchmark"),
    ("no_data_stats", "No data-backed statistics or research citations", "Data and citation enrichment", "🟡 Medium", "31-90 days", "weak_context"),
]


def scrape_page(url: str, session: requests.Session = None) -> dict:
    session = session or requests.Session()
    try:
        resp = session.get(url, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as e:
        return {"url": url, "status_code": None, "error": str(e), "ok": False}

    if resp.status_code == 404:
        return {"url": url, "status_code": 404, "ok": False, "error": "404 Not Found"}
    if resp.status_code >= 400:
        return {"url": url, "status_code": resp.status_code, "ok": False, "error": f"HTTP {resp.status_code}"}

    soup = BeautifulSoup(resp.text, "html.parser")
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(re.compile("^h[1-4]$"))]
    body_text = soup.get_text(" ", strip=True)
    word_count = len(body_text.split())
    first_150 = " ".join(body_text.split()[:150])

    has_faq = bool(re.search(r"\bfaq\b|frequently asked questions", resp.text, re.IGNORECASE))
    has_author = bool(soup.find(attrs={"rel": "author"}) or re.search(r"\bby\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b", body_text))
    has_date = bool(re.search(r"(last (updated|reviewed)|updated on|reviewed on)", resp.text, re.IGNORECASE))
    stat_count = len(re.findall(r"\b\d+(\.\d+)?\s?%|\b\d{2,}\b", body_text))

    return {
        "url": url, "status_code": resp.status_code, "ok": True,
        "headings": headings, "word_count": word_count, "first_150": first_150,
        "has_faq": has_faq, "has_author_or_date": has_author or has_date, "stat_count": stat_count,
        "body_text": body_text[:4000],  # capped — enough context for a remediation snippet, not the whole page
    }


def analyze_link(url: str, brand: str, session: requests.Session = None, target_prompts: list = None) -> dict:
    target_prompts = target_prompts or []
    scraped = scrape_page(url, session=session)
    if not scraped.get("ok"):
        return {
            "url": url, "scraped": scraped,
            "gaps": {g[0]: True for g in GAP_TYPES},  # an unreachable/404 page fails every check
            "note": f"Gap: The source is unreachable ({scraped.get('error', 'unknown error')}) and provides no "
                    f"substantive content to benchmark against.",
            "keywords": [], "semantic_density": 0.0, "condensed": "",
        }

    text_lower = scraped["body_text"].lower()
    gaps = {
        "brand_missing": brand.lower() not in text_lower,
        "no_question_headings": not any("?" in h for h in scraped["headings"]),
        "no_direct_answer_intro": "?" not in scraped["first_150"] and brand.lower() not in scraped["first_150"].lower(),
        "no_faq": not scraped["has_faq"],
        "weak_eeat": not scraped["has_author_or_date"],
        "no_data_stats": scraped["stat_count"] < 2,
    }

    # Keyword frequency + a quota-free TF-cosine "semantic density" proxy
    # (see text_analysis.py docstring for exactly what this does and doesn't
    # measure) — zero API calls, pure local Python.
    keywords = text_analysis.top_keywords(scraped["body_text"], n=15)
    semantic_density = text_analysis.semantic_density_score(scraped["body_text"], " ".join(target_prompts))
    gap_labels = {key: label for key, label, *_ in GAP_TYPES if gaps.get(key)}
    condensed = text_analysis.condensed_analysis(
        scraped["body_text"], target_prompts, scraped["headings"], gap_labels, max_keywords=8,
    )

    return {
        "url": url, "scraped": scraped, "gaps": gaps, "note": "",
        "keywords": keywords, "semantic_density": semantic_density, "condensed": condensed,
    }


REMEDIATION_PROMPT = """You are a GEO content strategist. A competitor page ({url}) was analysed for
the query cluster: {prompts}. Its detected weaknesses: {gap_labels}.
In 1-2 sentences, name the single biggest content gap on this page relative
to a strong {sector} educational resource, then a one-sentence recommended
action for {brand}. Prefix with "Gap:" and "→ Action:". Keep it under 60 words."""


def generate_remediation_snippet(link_analysis: dict, prompts: list, gap_labels: list,
                                   brand: str, sector: str, llm_client=None, llm_api_key: str = None) -> str:
    if link_analysis.get("note"):
        return link_analysis["note"]
    if not llm_client or not llm_api_key:
        return (
            f"Gap: {', '.join(gap_labels[:2]) or 'structural weaknesses'} identified on {link_analysis['url']}. "
            f"→ Action: Benchmark {brand}'s own page against this source and close the identified gaps."
        )
    prompt = REMEDIATION_PROMPT.format(
        url=link_analysis["url"], prompts=" | ".join(prompts[:3]),
        gap_labels=", ".join(gap_labels), brand=brand, sector=sector,
    )
    try:
        resp = llm_client.generate(prompt, llm_api_key)
        return resp.text.strip()
    except (QuotaExceededError, ProviderError):
        return (
            f"Gap: {', '.join(gap_labels[:2]) or 'structural weaknesses'} identified on {link_analysis['url']}. "
            f"→ Action: Benchmark {brand}'s own page against this source and close the identified gaps."
        )


def run_agent7(winning_links: list, brand: str, competitors: list, sector: str,
               triggering_prompts_by_link: dict = None, max_links: int = 3,
               llm_client=None, llm_api_key: str = None, session=None, log=None) -> dict:
    """winning_links: ordered list of URLs (Agent 6's top cited URLs), already
    capped by the caller's config but re-capped here defensively."""
    links = winning_links[:max_links]
    triggering_prompts_by_link = triggering_prompts_by_link or {}
    analyses = []
    for url in links:
        try:
            analyses.append(analyze_link(url, brand, session=session,
                                          target_prompts=triggering_prompts_by_link.get(url, [])))
        except Exception as e:  # noqa: BLE001 — one bad link must never abort the whole run
            if log:
                log(f"Source analysis failed for {url}: {e}")
            analyses.append({"url": url, "scraped": {"ok": False}, "gaps": {g[0]: True for g in GAP_TYPES},
                              "note": f"Gap: Could not analyse this source ({e}).",
                              "keywords": [], "semantic_density": 0.0, "condensed": ""})

    gap_rows = []
    all_triggering = sorted({p for prompts in triggering_prompts_by_link.values() for p in prompts})
    for i, (key, label, content_type, priority, timeline, benchmark_tag) in enumerate(GAP_TYPES, start=1):
        exhibiting_links = [a for a in analyses if a["gaps"].get(key)]
        if not exhibiting_links:
            continue
        prompts_for_gap = sorted({
            p for a in exhibiting_links for p in triggering_prompts_by_link.get(a["url"], all_triggering)
        })
        first = exhibiting_links[0]
        gap_labels = [GAP_TYPES[j][1] for j, g in enumerate(GAP_TYPES) if first["gaps"].get(GAP_TYPES[j][0])]
        snippet = generate_remediation_snippet(
            first, prompts_for_gap, gap_labels, brand, sector, llm_client=llm_client, llm_api_key=llm_api_key
        )
        gap_rows.append({
            "num": len(gap_rows) + 1,
            "issue": label,
            "triggering_prompts": " | ".join(prompts_for_gap[:5]),
            "signal_source": "Agent 7 - Source Analysis",
            "competitor_benchmark": ", ".join(competitors),
            "recommendation": _recommendation_for(key, brand),
            "content_type": content_type,
            "priority": priority,
            "timeline": timeline,
            "target_llms": "Gemini, ChatGPT",
            "impact_note": (
                f"{'High' if priority.startswith('🔴') else 'Medium'}-priority extractability gap across "
                f"{len(exhibiting_links)} analysed link(s); expected to improve eligibility for LLM citation "
                f"once content is updated. Evidence URLs: {' | '.join(a['url'] for a in exhibiting_links)}"
            ),
            "status": "Verified by page analysis (Agent 7)",
            "remediation_source_domain": "Reference source - see remediation brief",
            "remediation_source_role": benchmark_tag,
            "remediation_brief_snippet": snippet,
        })

    return {"gap_rows": gap_rows, "analyses": analyses}


def _recommendation_for(gap_key: str, brand: str) -> str:
    return {
        "brand_missing": f"Add clear, context-appropriate {brand} entity references and supporting facts where the page is meant to support brand visibility.",
        "no_question_headings": "Rewrite section headings as user questions so LLMs can extract direct answers and map the page to conversational prompts.",
        "no_direct_answer_intro": f"Add a concise answer-first introduction that directly answers the triggering query and names {brand} naturally where relevant.",
        "no_faq": "Add a compact FAQ block covering the exact prompt cluster, eligibility, risk caveats, and next-step actions.",
        "weak_eeat": "Add author/reviewer attribution, last reviewed date, methodology notes, and compliance-friendly proof points.",
        "no_data_stats": "Add verifiable data points and citations from regulator, industry, or owned research sources; avoid unsupported performance claims.",
    }[gap_key]
