"""Text helpers shared by service implementations."""

from __future__ import annotations

import re

WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(value: str) -> str:
    """Collapse repeated whitespace while preserving readable paragraphs."""

    normalized_lines = [WHITESPACE_RE.sub(" ", line).strip() for line in value.splitlines()]
    paragraphs = [line for line in normalized_lines if line]
    return "\n\n".join(paragraphs)

