"""Custom text analysis tools — lives in the project-root tools/ directory.

These are plain Python callables referenced from YAML via:
    ref: tools.text_tools.<function_name>

No pip install is required. Run `modular-agent-designer` from the project root
and the tools/ directory is automatically importable.
"""
from __future__ import annotations

import re


def word_count(text: str) -> dict:
    """Count words, sentences, and characters in `text`.

    Returns a dict with keys: words, sentences, characters.
    """
    words = len(text.split())
    sentences = len(re.findall(r"[.!?]+", text)) or 1
    return {"words": words, "sentences": sentences, "characters": len(text)}


def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """Extract the top N most frequent non-stopword words from `text`.

    Args:
        text: Input text to analyze.
        top_n: Number of keywords to return (default 5).

    Returns:
        List of the most frequent meaningful words, lowercased.
    """
    stopwords = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "it", "its",
        "this", "that", "these", "those", "i", "you", "he", "she", "we",
        "they", "my", "your", "his", "her", "our", "their", "as", "not",
    }
    tokens = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    freq: dict[str, int] = {}
    for token in tokens:
        if token not in stopwords:
            freq[token] = freq.get(token, 0) + 1
    return sorted(freq, key=lambda w: freq[w], reverse=True)[:top_n]


def summarize_stats(text: str) -> str:
    """Return a human-readable summary of text statistics.

    Combines word_count and extract_keywords into one string suitable for
    reporting back to the user via an LLM agent.
    """
    stats = word_count(text)
    keywords = extract_keywords(text, top_n=5)
    return (
        f"Text stats: {stats['words']} words, "
        f"{stats['sentences']} sentences, "
        f"{stats['characters']} characters. "
        f"Top keywords: {', '.join(keywords)}."
    )
