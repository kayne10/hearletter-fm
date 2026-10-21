"""Template-aware story extraction for newsletters with a stable layout.

The generic extractor in cleaning.py guesses stories from paragraph shape, which misfires on
newsletters it wasn't tuned for (sponsor blocks, preheaders, truncated stories). These parsers
read each newsletter's own markers instead. They take the forwarded text/plain body (Gmail
renders bold as *text*) and return stories in newsletter order, which is also the newsletter's
own order of importance. An empty result means "layout not recognised": callers fall back to
the generic extractor.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

INVISIBLE_RE = re.compile("[­͏؜ᅟᅠ឴឵᠎​-‏⁠-⁤﻿]")
URL_TOKEN_RE = re.compile(r"<https?://[^>\s]*>|\[image:[^\]]*\]", re.IGNORECASE)
SPACES_RE = re.compile(r"[ \t ]+")
MAX_STORY_WORDS = 180


@dataclass(frozen=True, slots=True)
class ParsedStory:
    title: str
    text: str
    section: str = ""


StoryParser = Callable[[str], list[ParsedStory]]


def strip_invisible(text: str) -> str:
    """Drop zero-width/preheader filler characters and non-breaking spaces."""

    return SPACES_RE.sub(" ", INVISIBLE_RE.sub("", text))


def normalized_lines(text: str) -> list[str]:
    """Lines with invisible characters, link tokens, and extra spaces removed (blanks kept)."""

    return [line.strip() for line in URL_TOKEN_RE.sub("", strip_invisible(text)).splitlines()]


def join_text(lines: list[str]) -> str:
    """Join wrapped lines into prose, dropping bold markers and bullet dashes."""

    words = " ".join(line for line in lines if line and line != "-").replace("*", " ").split()
    capped = words[:MAX_STORY_WORDS]
    text = " ".join(capped)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text + ("..." if len(words) > MAX_STORY_WORDS else "")


# --- TLDR ------------------------------------------------------------------------------------
# *Big Tech & Startups*
# *Apple Announces Oct. 28 Event for Touch-Screen MacBook Pro (2 minute read)*
# Apple will be holding a product launch event ...
# *Get 25% more from your training GPUs (Sponsor)*

TLDR_STORY_RE = re.compile(r"^\*(?P<title>.+?)\s*\((?P<kind>\d+ minute read|Sponsor)\)\s*\*$")
TLDR_END_MARKERS = ("Love TLDR?", "Want to advertise", "If you have any comments")


def parse_tldr(text: str) -> list[ParsedStory]:
    stories: list[ParsedStory] = []
    title: str | None = None
    is_sponsor = False
    section = ""
    body: list[str] = []

    def flush() -> None:
        if title and not is_sponsor and body:
            stories.append(ParsedStory(title=title, text=join_text(body), section=section))

    for span in bold_spans(normalized_lines(text)):
        if span.startswith(TLDR_END_MARKERS):
            break
        story = TLDR_STORY_RE.match(span)
        if story:
            flush()
            title, is_sponsor, body = story["title"].strip(), story["kind"] == "Sponsor", []
        elif span.startswith("*") and span.endswith("*") and len(span.split()) <= 6:
            flush()
            title, body = None, []
            section = span.strip("* ")
        elif title:
            body.append(span)
    flush()
    return stories


def bold_spans(lines: list[str]) -> list[str]:
    """Merge a bold title that wraps across lines ("*... (11" + "minute read)*") into one."""

    spans: list[str] = []
    pending: list[str] = []
    for line in lines:
        if pending:
            pending.append(line)
            if line.endswith("*"):
                spans.append(" ".join(pending))
                pending = []
            continue
        if line.startswith("*") and not line.endswith("*") and len(line) > 1:
            pending = [line]
        elif line:
            spans.append(line)
    spans.extend(pending)
    return spans


# --- The Rundown -----------------------------------------------------------------------------
# AI & SECURITY
# 🔓 *One hacker's AI agents break into 100+ companies
# *
# Image source: NorForge
# *The Rundown: *Security firm NorForge revealed ...
# *The details:*  - ...  *Why it matters: *...
# Sponsors use "TOGETHER WITH X" / "PRESENTED BY X"; "AI TRAINING" is a how-to, not news.

RUNDOWN_CATEGORY_RE = re.compile(r"^[A-Z0-9][A-Z0-9 &'’.,:/+\-]{2,}$")
RUNDOWN_SKIP_CATEGORIES = ("TOGETHER WITH", "PRESENTED BY", "SPONSORED", "AI TRAINING")
RUNDOWN_START = "LATEST DEVELOPMENTS"
RUNDOWN_END_MARKERS = ("QUICK HITS", "That's it for today", "That’s it for today")
RUNDOWN_LEAD = "The Rundown:"


def parse_rundown(text: str) -> list[ParsedStory]:
    lines = normalized_lines(text)
    start = next((i for i, line in enumerate(lines) if RUNDOWN_START in line), None)
    if start is None:
        return []

    blocks: list[tuple[str, list[str]]] = []
    for line in lines[start + 1 :]:
        if line.strip("* ").startswith(RUNDOWN_END_MARKERS):
            break
        if RUNDOWN_CATEGORY_RE.match(line):
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line)

    stories: list[ParsedStory] = []
    for category, block in blocks:
        if category.startswith(RUNDOWN_SKIP_CATEGORIES):
            continue
        story = rundown_story(category, block)
        if story is not None:
            stories.append(story)
    return stories


def rundown_story(category: str, block: list[str]) -> ParsedStory | None:
    """Lead paragraph and "Why it matters" first, details last, so the word cap trims details."""

    labels = [line.replace("*", "").strip() for line in block]
    lead = next((i for i, label in enumerate(labels) if label.startswith(RUNDOWN_LEAD)), None)
    if lead is None:
        return None
    details = next((i for i, label in enumerate(labels) if label.startswith("The details:")), None)
    why = next((i for i, label in enumerate(labels) if label.startswith("Why it matters:")), None)

    title_line = next((line for line in block[:lead] if "*" in line and line.strip("* ")), "")
    title = re.sub(r"^[^\w“\"']+", "", title_line.replace("*", " ")).strip()
    body_end = min(i for i in (details, why, len(block)) if i is not None and i > lead)
    parts = [join_text(block[lead:body_end]).removeprefix(RUNDOWN_LEAD).strip()]
    if why is not None:
        parts.append(join_text(block[why:]))
    if details is not None:
        parts.append(join_text(block[details : why if why and why > details else len(block)]))
    words = " ".join(part for part in parts if part).split()
    text = " ".join(words[:MAX_STORY_WORDS]) + ("..." if len(words) > MAX_STORY_WORDS else "")
    if not title or not text:
        return None
    return ParsedStory(title=title, text=text, section=category)


STORY_PARSERS: dict[str, StoryParser] = {
    "tldr": parse_tldr,
    "rundown": parse_rundown,
}
