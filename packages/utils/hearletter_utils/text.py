"""Text helpers shared by service implementations."""

from __future__ import annotations

import re

WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(value: str) -> str:
    """Collapse repeated whitespace while preserving readable paragraphs."""

    normalized_lines = [WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    paragraphs = [line for line in normalized_lines if line]
    return "\n\n".join(paragraphs)


def estimate_spoken_duration_seconds(text: str, words_per_minute: int = 155) -> int:
    """Estimate spoken duration for narration planning."""

    word_count = len(text.split())
    if word_count == 0:
        return 0
    return max(1, round((word_count / words_per_minute) * 60))

