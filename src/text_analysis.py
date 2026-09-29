"""
text_analysis.py — keyword frequency and semantic-density analysis.

Deliberately dependency-free (no scikit-learn/numpy) so it costs nothing in
API quota *or* install weight: everything here is stdlib (`collections`,
`re`, `math`). This is a real, honest limitation worth stating plainly —
"semantic density" here means term-frequency cosine similarity between the
scraped page and the target prompt, not a true embedding-based semantic
similarity (that would need an embeddings API call and a real vector
model). For the purpose this build needs it for — a quota-free signal for
"does this page actually talk about the same thing as the prompt, and how
much" — TF cosine similarity is a defensible, zero-cost proxy, not a
pretend-equivalent of real semantic embeddings.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# A small, generic English stopword list — enough to keep keyword/­density
# output meaningful without pulling in a stopword-list dependency. Not
# exhaustive; extend it for your vertical if you see junk keywords surface.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "so", "of",
    "to", "in", "on", "for", "with", "at", "by", "from", "up", "about",
    "into", "over", "after", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "shall", "should", "can", "could", "may", "might", "must", "this",
    "that", "these", "those", "it", "its", "you", "your", "yours", "we",
    "our", "ours", "they", "their", "them", "he", "she", "his", "her",
    "i", "me", "my", "not", "no", "as", "which", "what", "who", "whom",
    "when", "where", "why", "how", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "only", "own", "same", "just",
    "also", "there", "here", "out", "off", "again", "further", "once",
}

_TOKEN_RE = re.compile(r"[a-zA-Z']+")


def tokenize(text: str) -> list:
    if not text:
        return []
    return [
        w for w in (m.group(0).lower() for m in _TOKEN_RE.finditer(text))
        if len(w) > 2 and w not in STOPWORDS
    ]


def top_keywords(text: str, n: int = 15) -> list:
    """Ranked keyword list with raw count and density (% of all counted
    tokens on the page) — pure `collections.Counter`, zero API calls."""
    tokens = tokenize(text)
    total = len(tokens) or 1
    counts = Counter(tokens)
    return [
        {"keyword": word, "count": count, "density_pct": round(count / total * 100, 2)}
        for word, count in counts.most_common(n)
    ]


def _term_frequencies(tokens: list) -> dict:
    total = len(tokens) or 1
    counts = Counter(tokens)
    return {w: c / total for w, c in counts.items()}


def semantic_density_score(page_text: str, target_text: str) -> float:
    """Cosine similarity between the page's and the target prompt's term-
    frequency vectors, in [0, 1]. This is the quota-free proxy described in
    the module docstring — a real embedding-based semantic score would need
    an API call this build intentionally avoids here."""
    page_tf = _term_frequencies(tokenize(page_text))
    target_tf = _term_frequencies(tokenize(target_text))
    if not page_tf or not target_tf:
        return 0.0
    shared_vocab = set(page_tf) | set(target_tf)
    dot = sum(page_tf.get(w, 0.0) * target_tf.get(w, 0.0) for w in shared_vocab)
    page_norm = math.sqrt(sum(v * v for v in page_tf.values())) or 1.0
    target_norm = math.sqrt(sum(v * v for v in target_tf.values())) or 1.0
    return round(dot / (page_norm * target_norm), 3)


def condensed_analysis(
    page_text: str,
    target_prompts: list,
    headings: list,
    gaps: dict,
    max_keywords: int = 8,
) -> str:
    """A short, prompt-safe text block summarising a scraped page's keyword
    profile + structural gaps — used to feed Agent 8 instead of the raw
    scraped page. Raw competitor pages run 1,000-4,000+ words; stuffing that
    into a single Groq call could burn a large share of an 8,000
    tokens/minute budget by itself. This block is capped at a small, fixed
    number of keywords/headings regardless of page length, so a single
    Agent 8 call stays cheap no matter how long the source page was."""
    target_text = " ".join(target_prompts)
    keywords = top_keywords(page_text, n=max_keywords)
    density = semantic_density_score(page_text, target_text)
    missing = [label for key, label in gaps.items() if label] if isinstance(gaps, dict) else []

    kw_line = ", ".join(f"{k['keyword']} ({k['density_pct']}%)" for k in keywords) or "none extracted"
    heading_sample = " | ".join(headings[:5]) if headings else "none found"

    return (
        f"Top keywords on the existing page: {kw_line}.\n"
        f"Topical alignment with target prompt(s) (0-1 TF cosine similarity): {density}.\n"
        f"Existing headings (sample): {heading_sample}.\n"
        f"Structural gaps to close: {'; '.join(missing) if missing else 'none detected'}."
    )
