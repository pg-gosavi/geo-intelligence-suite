"""
Agent 8 — Competitive Content Strategy + Draft Generation (Module D).

The old Agent 8 jumped almost directly from competitor gaps to an article.
That produced generic "complete guide" copy, which is useful as prose but is
not a strategy a brand can use to deliberately beat a competing resource.

This version separates the job into two layers without changing the public
Agent 8 return shape:

1. Build a deterministic, evidence-backed COMPETITIVE CONTENT STRATEGY from
   Agent 7's analysis. This identifies the competitor's weaknesses, the
   opportunity, the recommended content angle, differentiators, required
   sections, evidence needs, internal-link/CTA opportunities, and explicit
   win conditions.
2. Generate the article from that strategy. The model is instructed to make
   the article materially better, not merely longer, and never to invent
   facts, prices, rates, claims, statistics, or competitor details.

No additional LLM call is required for the strategy. This matters because
Agent 8 runs in the same quota-sensitive pipeline as the other agents.
The strategy is produced locally from the already-computed Agent 7 signals,
then injected into the existing single draft-generation call.

Backward compatibility:
- run_agent8(...) keeps its existing signature.
- strategy_rows retain the exact keys used by the Excel exporter/schema.
- drafts remains a filename -> markdown mapping.
- The explicit-empty selection behaviour remains unchanged.
"""
from __future__ import annotations

from urllib.parse import urlparse

from ..providers.base import ProviderError, QuotaExceededError


CONTENT_TYPE_BY_INTENT = {
    "application_process": "Step-by-step guide",
    "recommendation": "Decision guide + comparison",
    "comparison": "Comparison / decision page",
    "fees_pricing": "Cost + value comparison",
    "eligibility": "Answer-first eligibility guide",
    "category_research": "Category guide + decision framework",
    "problem_discovery": "Problem-solving guide + comparison",
}

PRIORITY_ORDER = {"High": 3, "Medium": 2, "Low": 1}

# These are intentionally phrased as actions a content/marketing team can
# implement. They are selected from observed Agent 7 gaps rather than generic
# SEO boilerplate.
GAP_ACTIONS = {
    "brand_missing": (
        "Earn brand/entity relevance by introducing the brand only where it is "
        "genuinely useful to the reader, using a factual use-case or decision rule."
    ),
    "no_question_headings": (
        "Rebuild the heading hierarchy around the questions users and LLMs are likely "
        "to ask, then answer each question immediately below the heading."
    ),
    "no_direct_answer_intro": (
        "Open with a compact answer that resolves the primary question before the deeper explanation."
    ),
    "no_faq": (
        "Add a focused FAQ covering unresolved edge cases and high-intent follow-up questions, "
        "not a generic list of FAQs."
    ),
    "weak_eeat": (
        "Add visible author/expert-review and last-reviewed signals where the publishing workflow "
        "can support them; do not fabricate credentials."
    ),
    "no_data_stats": (
        "Support consequential claims with current, traceable evidence and clearly label dates, "
        "methodology, assumptions, and source context."
    ),
}

GAP_PRIORITIES = {
    "brand_missing": "High",
    "no_question_headings": "High",
    "no_direct_answer_intro": "High",
    "no_faq": "High",
    "weak_eeat": "High",
    "no_data_stats": "Medium",
}

# Fallback path (no winning link to calibrate against).
DRAFT_PROMPT = """Write a {word_target}-word article for {brand} ({sector}, {region}) that directly answers:

\"{prompt}\"

The goal is to create a resource that is more useful and more citable by AI
search systems than generic pages on the same topic.

Requirements:
- Open with a direct answer in 2-3 sentences.
- Use clear question-led H2/H3 headings where natural.
- Include decision criteria, examples, and a focused FAQ where useful.
- Mention {brand} naturally and accurately; never invent product facts,
  performance numbers, rates, rankings, regulatory claims, or customer results.
- Prefer precise, verifiable statements over marketing language.
- Plain markdown. No preamble outside the article.
"""


DRAFT_PROMPT_CALIBRATED = """Write a {word_target}-word article for {brand} ({sector}, {region}) targeting this reader question:

\"{prompt}\"

Your objective is not to produce a longer version of the competitor. Your objective
is to produce a materially more useful, more decision-oriented, more evidence-ready,
and more AI-citable resource.

TARGET COMPETITOR
{competitor_domain}

COMPETITIVE STRATEGY TO EXECUTE
---
{strategy}
---

CONDENSED COMPETITOR ANALYSIS
---
{condensed_analysis}
---

CONTENT RULES
- Lead with a direct answer that resolves the main question immediately.
- Build the page around the recommended content angle and win conditions in the strategy.
- Cover the primary user decision, not just the topic definition.
- Use question-led H2/H3 headings where they improve retrieval and clarity.
- Include concrete examples, decision rules, caveats, and comparison criteria where the strategy calls for them.
- Include an FAQ only when it addresses real follow-up intent; avoid filler questions.
- Mention {brand} naturally only where it has a legitimate role in the answer.
- Do not mention the competitor, its domain, or this strategy in the published article.
- Never fabricate prices, rates, dates, rankings, statistics, regulations, product features,
  credentials, reviews, or performance claims. When evidence is required but not available,
  write a clearly marked editorial placeholder such as [ADD VERIFIED 2026 SOURCE].
- Do not claim that the article will "rank #1", "beat Google", or guarantee AI citations.
- Plain markdown, with the article title as the first line and no meta commentary.
"""


def pick_priority_prompts(execution_rows: list, citation_ranking_rows: list, brand: str, max_drafts: int) -> list:
    """Fallback selection when no winning links are available to calibrate against."""
    scored = []
    for exec_row, rank_row in zip(execution_rows, citation_ranking_rows):
        brand_cited_anywhere = any(
            exec_row.get("per_llm", {}).get(llm, {}).get("mentioned", {}).get(brand)
            for llm in exec_row.get("per_llm", {})
        )
        # rank_row is deliberately read so the function preserves the existing
        # call contract; priority remains primarily driven by brand absence.
        _ = rank_row
        score = 0 if brand_cited_anywhere else 1
        scored.append((score, exec_row))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [row for _, row in scored[:max_drafts]]


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "") or url
    except ValueError:
        return url


def _word_target(link_analysis: dict) -> int:
    """Set a bounded target based on the competitor, but never equate length
    with quality. This is a target for the generator, not an enforced minimum."""
    existing = (link_analysis.get("scraped") or {}).get("word_count") or 700
    return max(650, min(1500, int(existing * 1.10)))


def _title_for_link(prompt_text: str) -> str:
    """Use the user intent as the title rather than manufacturing a generic
    'More Complete Guide' claim."""
    base = (prompt_text or "Comprehensive answer").strip().strip("?")
    base = base[0].upper() + base[1:] if base else "Comprehensive answer"
    return base


def _safe_join(values, separator=" | ", default="None identified"):
    values = [str(v).strip() for v in values if str(v).strip()]
    return separator.join(values) if values else default


def _gap_items(link_analysis: dict) -> list[tuple[str, str, str]]:
    """Return detected gaps as (key, priority, action), highest-value first."""
    gaps = link_analysis.get("gaps") or {}
    items = []
    for key, value in gaps.items():
        if not value:
            continue
        priority = GAP_PRIORITIES.get(key, "Medium")
        action = GAP_ACTIONS.get(key, "Close the observed structural/content gap.")
        items.append((key, priority, action))
    items.sort(key=lambda x: (-PRIORITY_ORDER.get(x[1], 1), x[0]))
    return items


def _gap_label_from_analysis(link_analysis: dict, key: str) -> str:
    labels = {
        "brand_missing": "Brand/entity coverage gap",
        "no_question_headings": "Question-led heading gap",
        "no_direct_answer_intro": "Direct-answer gap",
        "no_faq": "FAQ coverage gap",
        "weak_eeat": "E-E-A-T/trust-signal gap",
        "no_data_stats": "Evidence/data gap",
    }
    return labels.get(key, key.replace("_", " ").title())


def _extract_topics(link_analysis: dict) -> list[str]:
    keywords = link_analysis.get("keywords") or []
    topics = []
    for item in keywords[:10]:
        if isinstance(item, dict):
            word = item.get("keyword")
        else:
            word = str(item)
        if word:
            topics.append(str(word).strip())
    return topics


def _question_candidates(prompt_text: str, intent_category: str, topics: list[str]) -> list[str]:
    """Generate useful section questions without making factual claims."""
    questions = []
    prompt = (prompt_text or "").strip().strip("?")
    if prompt:
        questions.append(prompt + "?")

    generic_by_intent = {
        "recommendation": [
            "Which option fits different user profiles?",
            "What should I compare before choosing?",
            "When is the higher-cost option actually worth it?",
        ],
        "comparison": [
            "What are the most important differences between the options?",
            "Which option is best for different use cases?",
            "What are the trade-offs and hidden costs?",
        ],
        "fees_pricing": [
            "What does the total cost look like over a year?",
            "When do the benefits justify the fee?",
            "Which costs are easy to overlook?",
        ],
        "eligibility": [
            "Who is eligible and what can affect approval?",
            "Which requirements should applicants check first?",
            "What should someone do if they do not qualify immediately?",
        ],
        "application_process": [
            "What is the application process step by step?",
            "Which documents or information are usually needed?",
            "What mistakes commonly slow the process down?",
        ],
    }
    questions.extend(generic_by_intent.get(intent_category, [
        "What should a reader compare before deciding?",
        "Which option fits different real-world situations?",
        "What trade-offs should be understood before acting?",
    ]))

    if topics:
        questions.append(f"How do {topics[0]} and {topics[1] if len(topics) > 1 else 'the key criteria'} affect the decision?")

    # preserve order + de-duplicate
    return list(dict.fromkeys(questions))[:6]


def _build_content_strategy(link_analysis: dict, prompt_text: str, intent_category: str,
                            brand: str, sector: str, region: str, competitors: list) -> dict:
    """Build a practical strategy object from Agent 7 signals.

    The score is intentionally a heuristic prioritisation signal, not a
    prediction of rankings or citations.
    """
    scraped = link_analysis.get("scraped") or {}
    domain = _domain_of(link_analysis.get("url", ""))
    gaps = _gap_items(link_analysis)
    topics = _extract_topics(link_analysis)
    prompts = [p for p in [prompt_text] if p]

    gap_count = len(gaps)
    gap_rate = gap_count / max(1, len(GAP_ACTIONS))
    alignment = float(link_analysis.get("semantic_density") or 0.0)
    brand_missing = bool((link_analysis.get("gaps") or {}).get("brand_missing"))

    # A prioritisation heuristic. It expresses how much room the analysed page
    # leaves for a better resource. It is NOT a forecast of ranking/citation.
    opportunity_score = round(min(100.0, (alignment * 30) + (gap_rate * 45) + (15 if brand_missing else 0) + (10 if not scraped.get("has_author_or_date") else 0)))

    high_gaps = [
        _gap_label_from_analysis(link_analysis, key)
        for key, priority, _ in gaps if priority == "High"
    ]
    all_gap_actions = [action for _, _, action in gaps]

    if intent_category in {"recommendation", "comparison", "fees_pricing"}:
        angle = (
            "Turn the topic into a decision framework: show readers how to choose based on "
            "use case, cost/value, constraints, and trade-offs rather than presenting a generic list."
        )
    elif intent_category == "eligibility":
        angle = (
            "Turn the page into an answer-first eligibility and next-step guide, separating "
            "general qualification factors from brand-specific details that require verification."
        )
    elif intent_category == "application_process":
        angle = (
            "Turn the page into an execution-focused process guide with steps, prerequisites, "
            "common failure points, and practical checks before the reader starts."
        )
    else:
        angle = (
            "Build a problem-solving guide that answers the primary question quickly and then "
            "adds the comparisons, examples, caveats, and evidence a reader needs to act."
        )

    # We want explicit competitive proof points rather than vague "add more detail" advice.
    differentiators = [
        "Answer-first opening that resolves the primary question immediately.",
        "Decision rules and scenario-based recommendations instead of a generic feature list.",
        "Transparent comparison criteria, including trade-offs and total-cost/value logic where relevant.",
        "Evidence-ready claims with a visible source/date workflow for consequential facts.",
        "Question-led structure that maps to real follow-up intent and retrieval patterns.",
    ]
    if "Brand/entity coverage gap" in high_gaps:
        differentiators.append(
            f"Use {brand} as a relevant entity in a helpful decision context, not as an unsupported promotional insert."
        )
    if "E-E-A-T/trust-signal gap" in high_gaps:
        differentiators.append("Add verifiable author/reviewer and last-reviewed signals where editorial governance supports them.")

    required_sections = _question_candidates(prompt_text, intent_category, topics)

    evidence_plan = [
        "Verify every current price, fee, rate, eligibility threshold, policy, product feature, or dated claim before publication.",
        "Prefer primary/official sources for product, regulatory, pricing, and policy facts.",
        "For any quantitative claim, record source, publication/updated date, and the exact value being supported.",
    ]

    brand_role = (
        f"Position {brand} as a useful, factual option within the decision framework where it genuinely fits; "
        "do not force a brand mention if the page intent does not support one."
    )

    win_conditions = [
        "A reader can answer the main question without scanning the full page.",
        "A reader can determine which option/use case fits them using explicit rules or scenarios.",
        "The page closes the important structural weaknesses detected on the benchmark page.",
        "Important claims are evidence-ready and the page contains no unverifiable competitive assertions.",
        "The article provides a distinct information gain rather than simply adding words."
    ]

    return {
        "target_query": prompt_text or "(not specified)",
        "competitor_domain": domain,
        "competitor_url": link_analysis.get("url", ""),
        "opportunity_score": opportunity_score,
        "score_note": "Heuristic prioritisation signal derived from Agent 7; not a ranking or citation forecast.",
        "intent": intent_category,
        "content_type": CONTENT_TYPE_BY_INTENT.get(intent_category, "Answer-first guide"),
        "content_angle": angle,
        "competitor_strengths_to_match": [
            "Retain the competitor's topical relevance where the extracted keywords show clear alignment.",
            "Cover the core topic expected by the target query before introducing differentiators.",
        ],
        "competitor_weaknesses": high_gaps or ["No major structural weakness detected by Agent 7; compete through usefulness and evidence."],
        "gap_actions": all_gap_actions or ["No detected Agent 7 gap; focus on information gain, evidence, and decision utility."],
        "topical_entities": topics or ["Use the target query and verified domain terminology."],
        "recommended_sections": required_sections,
        "differentiators": differentiators,
        "evidence_plan": evidence_plan,
        "brand_role": brand_role,
        "win_conditions": win_conditions,
        "target_llms": ["Gemini", "ChatGPT"],
        "priority": "High" if opportunity_score >= 55 else "Medium",
        "estimated_length": _word_target(link_analysis),
        "competitors": competitors or [],
        "sector": sector,
        "region": region,
        "scraped_word_count": scraped.get("word_count"),
        "semantic_alignment": alignment,
        "detected_gap_count": gap_count,
    }


def _strategy_markdown(strategy: dict, title: str, brand: str) -> str:
    """Human-readable strategy document used as an artifact and audit trail."""
    competitors = ", ".join(strategy.get("competitors") or []) or "Top visible competitors"
    lines = [
        f"# Competitive Content Strategy — {title}",
        "",
        f"**Brand:** {brand}",
        f"**Target query:** {strategy['target_query']}",
        f"**Primary competitor:** {strategy['competitor_domain']}",
        f"**Intent:** {strategy['intent']}",
        f"**Content type:** {strategy['content_type']}",
        f"**Opportunity score:** {strategy['opportunity_score']}/100",
        f"**Score interpretation:** {strategy['score_note']}",
        f"**Target competitors supplied to run:** {competitors}",
        "",
        "## 1. Competitive objective",
        strategy["content_angle"],
        "",
        "## 2. What we should match",
    ]
    lines.extend([f"- {x}" for x in strategy["competitor_strengths_to_match"]])
    lines.extend(["", "## 3. What the competitor leaves open", ""]) 
    lines.extend([f"- {x}" for x in strategy["competitor_weaknesses"]])
    lines.extend(["", "## 4. Exact remediation actions", ""])
    lines.extend([f"- {x}" for x in strategy["gap_actions"]])
    lines.extend(["", "## 5. Recommended page structure", ""])
    for idx, q in enumerate(strategy["recommended_sections"], start=1):
        lines.append(f"{idx}. {q}")
    lines.extend(["", "## 6. Differentiation required to beat the benchmark", ""])
    lines.extend([f"- {x}" for x in strategy["differentiators"]])
    lines.extend(["", "## 7. Evidence and trust plan", ""])
    lines.extend([f"- {x}" for x in strategy["evidence_plan"]])
    lines.extend(["", "## 8. Brand role", "", strategy["brand_role"]])
    lines.extend(["", "## 9. Win conditions", ""])
    lines.extend([f"- {x}" for x in strategy["win_conditions"]])
    lines.extend([
        "",
        "## 10. Editorial guardrails",
        "",
        "- Do not copy the competitor's wording or structure verbatim.",
        "- Do not make unsupported claims about ranking, citation, superiority, or market position.",
        "- Replace every evidence placeholder with a verified source before publication.",
        "- Treat the opportunity score as prioritisation only, not as a performance prediction.",
    ])
    return "\n".join(lines).strip() + "\n"


def _build_strategy_brief(strategy: dict) -> str:
    """Compact strategy string injected into the single LLM drafting call."""
    def bullets(items):
        return "\n".join(f"- {x}" for x in items)

    return "\n".join([
        f"Opportunity score: {strategy['opportunity_score']}/100 ({strategy['score_note']})",
        f"Content angle: {strategy['content_angle']}",
        f"Competitor weaknesses: {bullets(strategy['competitor_weaknesses'])}",
        f"Actions: {bullets(strategy['gap_actions'])}",
        f"Topical entities/terms to cover: {', '.join(strategy['topical_entities'])}",
        f"Recommended questions/sections: {bullets(strategy['recommended_sections'])}",
        f"Differentiators: {bullets(strategy['differentiators'])}",
        f"Evidence plan: {bullets(strategy['evidence_plan'])}",
        f"Brand role: {strategy['brand_role']}",
        f"Win conditions: {bullets(strategy['win_conditions'])}",
    ])


def _draft_from_link(i: int, link_analysis: dict, prompt_text: str, intent_category: str,
                     brand: str, sector: str, region: str, competitors: list,
                     llm_client, llm_api_key, log=None):
    domain = _domain_of(link_analysis.get("url", ""))
    title = _title_for_link(prompt_text)
    content_type = CONTENT_TYPE_BY_INTENT.get(intent_category, "Answer-first page section")
    strategy = _build_content_strategy(
        link_analysis, prompt_text, intent_category, brand, sector, region, competitors
    )
    strategy_md = _strategy_markdown(strategy, title, brand)
    strategy_brief = _build_strategy_brief(strategy)
    word_target = _word_target(link_analysis)
    condensed = link_analysis.get("condensed") or "No condensed analysis available for this link."

    draft_text = f"# {title}\n\n*Draft not generated — no LLM key configured for Agent 8.*"
    if llm_client and llm_api_key:
        try:
            resp = llm_client.generate(
                DRAFT_PROMPT_CALIBRATED.format(
                    brand=brand,
                    sector=sector,
                    region=region,
                    prompt=prompt_text or title,
                    word_target=word_target,
                    competitor_domain=domain,
                    strategy=strategy_brief,
                    condensed_analysis=condensed,
                ),
                llm_api_key,
            )
            body = (resp.text or "").strip()
            draft_text = f"# {title}\n\n{body}" if body else f"# {title}\n\n*No draft text returned by the LLM.*"
        except QuotaExceededError as e:
            if log:
                log(f"Draft generation quota exhausted at article {i}: {e}")
        except ProviderError as e:
            if log:
                log(f"Draft generation failed for '{title}': {e}")

    # Keep the existing row keys so the current workbook exporter keeps working.
    # title_brief is now an actual strategic brief instead of a title-only label.
    title_brief = (
        f"{title}\n"
        f"Angle: {strategy['content_angle']}\n"
        f"Opportunity score: {strategy['opportunity_score']}/100 (heuristic)\n"
        f"Competitor weaknesses: {_safe_join(strategy['competitor_weaknesses'])}\n"
        f"Differentiators: {_safe_join(strategy['differentiators'])}\n"
        f"Recommended structure: {_safe_join(strategy['recommended_sections'])}\n"
        f"Evidence plan: {_safe_join(strategy['evidence_plan'])}\n"
        f"Win conditions: {_safe_join(strategy['win_conditions'])}"
    )

    strategy_row = {
        "num": i,
        "title_brief": title_brief,
        "content_type": content_type,
        "target_prompts": prompt_text or "(no specific triggering prompt on file)",
        "primary_intent": intent_category,
        "competitor_to_outperform": domain,
        "target_llm_citation": ", ".join(strategy["target_llms"]),
        "priority": strategy["priority"],
    }
    return strategy_row, draft_text, strategy_md


def _fallback_strategy(prompt_text: str, intent_category: str, brand: str, sector: str,
                       region: str, competitors: list) -> tuple[dict, str]:
    """Create a useful strategy even when no competitor page is available."""
    title = _title_for_link(prompt_text)
    strategy = {
        "target_query": prompt_text or "(not specified)",
        "competitor_domain": "Top visible competitors",
        "competitor_url": "",
        "opportunity_score": 50,
        "score_note": "Fallback heuristic because no selected winning page was available for page-level benchmarking.",
        "intent": intent_category,
        "content_type": CONTENT_TYPE_BY_INTENT.get(intent_category, "Answer-first guide"),
        "content_angle": "Own the query with a direct, decision-oriented resource that is more useful than a generic overview.",
        "competitor_strengths_to_match": ["Cover the obvious core intent before differentiating."],
        "competitor_weaknesses": ["Page-level competitor weaknesses were not available in this fallback path."],
        "gap_actions": [
            "Use a direct-answer opening.",
            "Use question-led sections around real decision points.",
            "Add explicit trade-offs, scenarios, and evidence requirements.",
        ],
        "topical_entities": [],
        "recommended_sections": _question_candidates(prompt_text, intent_category, []),
        "differentiators": [
            "Decision framework instead of generic description.",
            "Scenario-based recommendations.",
            "Transparent evidence and verification requirements.",
        ],
        "evidence_plan": [
            "Verify current quantitative and policy claims before publication.",
            "Use primary/official sources where possible.",
        ],
        "brand_role": f"Mention {brand} only where it is genuinely relevant and factually supported.",
        "win_conditions": [
            "The main question is answered immediately.",
            "Readers can make a decision using explicit criteria.",
            "Important claims are evidence-ready.",
        ],
        "target_llms": ["Gemini", "ChatGPT"],
        "priority": "High",
        "estimated_length": 800,
        "competitors": competitors or [],
        "sector": sector,
        "region": region,
        "scraped_word_count": None,
        "semantic_alignment": 0.0,
        "detected_gap_count": 0,
    }
    return strategy, _strategy_markdown(strategy, title, brand)


def run_agent8(execution_rows: list, citation_ranking_rows: list, brand: str, competitors: list,
               sector: str, region: str, max_drafts: int = 3,
               link_analyses: list = None, triggering_prompts_by_link: dict = None,
               allow_fallback: bool = True,
               llm_client=None, llm_api_key: str = None, log=None) -> dict:
    strategy_rows = []
    drafts = {}  # filename -> markdown text
    strategy_documents = {}  # filename -> detailed competitive strategy
    triggering_prompts_by_link = triggering_prompts_by_link or {}

    if link_analyses:
        # Primary path: one strategic content package per selected/analysed link.
        chosen_links = link_analyses[:max_drafts]
        for i, link_analysis in enumerate(chosen_links, start=1):
            prompts_for_link = triggering_prompts_by_link.get(link_analysis.get("url"), [])
            prompt_text = prompts_for_link[0] if prompts_for_link else ""
            intent_category = "recommendation"
            for row in execution_rows:
                if row.get("conversational_prompt") == prompt_text:
                    intent_category = row.get("intent_category", intent_category)
                    break

            strategy_row, draft_text, strategy_md = _draft_from_link(
                i, link_analysis, prompt_text, intent_category, brand, sector, region, competitors,
                llm_client, llm_api_key, log=log,
            )
            strategy_rows.append(strategy_row)

            safe_domain = (_domain_of(link_analysis.get("url", "source")) or "source").replace(".", "_")
            drafts[f"article_{i:02d}_{safe_domain}.md"] = draft_text
            strategy_documents[f"content_strategy_{i:02d}_{safe_domain}.md"] = strategy_md

        return {"strategy_rows": strategy_rows, "drafts": drafts, "strategy_documents": strategy_documents}

    if not allow_fallback:
        # Explicitly selected zero links: do not substitute unrelated prompts.
        return {"strategy_rows": [], "drafts": {}, "strategy_documents": {}}

    # Fallback path: preserve previous behaviour of drafting against the most
    # important absent-brand prompts, but now still produce a concrete strategy
    # artifact instead of only a generic article.
    chosen = pick_priority_prompts(execution_rows, citation_ranking_rows, brand, max_drafts)
    for i, row in enumerate(chosen, start=1):
        prompt_text = row["conversational_prompt"]
        title = _title_for_link(prompt_text)
        intent_category = row.get("intent_category", "recommendation")
        strategy, strategy_md = _fallback_strategy(prompt_text, intent_category, brand, sector, region, competitors)

        draft_text = f"# {title}\n\n*Draft not generated — no LLM key configured for Agent 8.*"
        if llm_client and llm_api_key:
            try:
                resp = llm_client.generate(
                    DRAFT_PROMPT.format(
                        brand=brand, sector=sector, region=region,
                        prompt=prompt_text, word_target=strategy["estimated_length"],
                    ),
                    llm_api_key,
                )
                body = (resp.text or "").strip()
                draft_text = f"# {title}\n\n{body}" if body else f"# {title}\n\n*No draft text returned by the LLM.*"
            except QuotaExceededError as e:
                if log:
                    log(f"Draft generation quota exhausted at article {i}/{len(chosen)}: {e}")
            except ProviderError as e:
                if log:
                    log(f"Draft generation failed for '{title}': {e}")

        title_brief = (
            f"{title}\n"
            f"Angle: {strategy['content_angle']}\n"
            f"Opportunity score: {strategy['opportunity_score']}/100 (heuristic)\n"
            f"Differentiators: {_safe_join(strategy['differentiators'])}\n"
            f"Recommended structure: {_safe_join(strategy['recommended_sections'])}\n"
            f"Win conditions: {_safe_join(strategy['win_conditions'])}"
        )
        strategy_rows.append({
            "num": i,
            "title_brief": title_brief,
            "content_type": strategy["content_type"],
            "target_prompts": prompt_text,
            "primary_intent": intent_category,
            "competitor_to_outperform": ", ".join(competitors) if competitors else "Top visible competitors",
            "target_llm_citation": ", ".join(strategy["target_llms"]),
            "priority": strategy["priority"],
        })

        drafts[f"article_{i:02d}_{intent_category}.md"] = draft_text
        strategy_documents[f"content_strategy_{i:02d}_{intent_category}.md"] = strategy_md

    return {"strategy_rows": strategy_rows, "drafts": drafts, "strategy_documents": strategy_documents}
