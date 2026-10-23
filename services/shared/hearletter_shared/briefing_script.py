"""Compose one spoken morning-briefing script from several cleaned newsletters."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from hearletter_domain.briefing import (
    BriefingConfig,
    BriefingScript,
    StagedNewsletter,
    Story,
    briefing_fingerprint,
    episode_id_for,
)

PLACEHOLDER_TITLES = {"story candidate"}
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
STORY_LEADS = ("First", "Also worth knowing", "And one more")
MAX_SUMMARY_WORDS = 70
ABBREVIATIONS = {"sen.", "rep.", "gov.", "pres.", "mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "vs.",
                 "inc.", "corp.", "co.", "u.s.", "u.k.", "e.g.", "i.e."}


def story_from_candidate(candidate: dict[str, object]) -> Story:
    """Turn a cleaner story candidate into a speakable story."""

    title = spoken_text(str(candidate.get("title", ""))).rstrip(".:")
    if title.lower() in PLACEHOLDER_TITLES:
        title = ""
    excerpt = str(candidate.get("summary_source_text", ""))
    return Story(title=title, summary=spoken_summary(excerpt), source_text=spoken_text(excerpt))


def story_from_parsed(title: str, text: str, section: str = "") -> Story:
    """Turn a template-parser story into a speakable story."""

    return Story(
        title=spoken_text(title).rstrip(".:"),
        summary=spoken_summary(text),
        source_text=spoken_text(text),
        section=spoken_text(section),
    )


def spoken_summary(excerpt: str, *, max_sentences: int = 2) -> str:
    """First couple of sentences of an excerpt, capped for listening."""

    sentences = split_sentences(spoken_text(excerpt))
    summary = " ".join(sentences[:max_sentences]).strip()
    words = summary.split()
    if len(words) > MAX_SUMMARY_WORDS:
        summary = " ".join(words[:MAX_SUMMARY_WORDS]).rstrip(",;:") + "."
    return summary


def split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation, but not after titles like "Sen." or "Dr."."""

    sentences: list[str] = []
    for piece in SENTENCE_END_RE.split(text):
        previous_words = sentences[-1].split() if sentences else []
        if previous_words and previous_words[-1].lower() in ABBREVIATIONS:
            sentences[-1] = f"{sentences[-1]} {piece}"
        else:
            sentences.append(piece)
    return [sentence for sentence in sentences if sentence]


def build_briefing_script(
    *,
    newsletters: Sequence[StagedNewsletter],
    config: BriefingConfig,
    forwarder: str,
    episode_date: str,
    created_at: str,
    version: int = 1,
) -> BriefingScript:
    """Order newsletters by config and render a single plain-text script.

    Paragraphs are separated by blank lines; the TTS stage chunks on those boundaries.
    """

    ordered = order_newsletters(newsletters, config)
    names = [item.source_name for item in ordered]
    fingerprint = briefing_fingerprint(ordered)

    paragraphs = [
        f"Good morning. This is your Hearletter briefing for {spoken_date(episode_date)}.",
        f"Today we're pulling from {join_names(names)}.",
    ]
    for index, newsletter in enumerate(ordered):
        paragraphs.append(source_intro(newsletter.source_name, index, len(ordered)))
        stories = newsletter.stories[: config.max_stories_per_source]
        if not stories:
            paragraphs.append(f"{newsletter.source_name} was light on news today.")
        for rank, story in enumerate(stories):
            paragraphs.append(story_paragraph(story, rank))
    paragraphs.append("That's your briefing. Have a great day.")

    return BriefingScript(
        episode_id=episode_id_for(forwarder, episode_date, fingerprint),
        title=title_for_version(config.title_for(episode_date), version),
        episode_date=episode_date,
        forwarder=forwarder,
        sources=tuple(item.source_email for item in ordered),
        source_names=tuple(names),
        text="\n\n".join(paragraph for paragraph in paragraphs if paragraph.strip()),
        created_at=created_at,
        fingerprint=fingerprint,
        version=version,
    )


def title_for_version(title: str, version: int) -> str:
    return title if version <= 1 else f"{title} (update {version - 1})"


def order_newsletters(
    newsletters: Sequence[StagedNewsletter], config: BriefingConfig
) -> list[StagedNewsletter]:
    """Newsletters in the order their sources appear in the config."""

    source_order = {source.email: index for index, source in enumerate(config.sources)}
    return sorted(newsletters, key=lambda item: source_order.get(item.source_email, 99))


def source_intro(name: str, index: int, total: int) -> str:
    if index == 0:
        return f"Let's start with {name}."
    if index == total - 1:
        return f"And finally, here's {name}."
    return f"Next up, {name}."


def story_paragraph(story: Story, rank: int) -> str:
    lead = STORY_LEADS[min(rank, len(STORY_LEADS) - 1)]
    if story.title:
        return f"{lead}: {story.title}. {story.summary}".strip()
    return f"{lead}: {story.summary}".strip()


def join_names(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])}, and {names[-1]}" if len(names) > 2 else " and ".join(names)


def spoken_date(iso_date: str) -> str:
    value = date.fromisoformat(iso_date)
    return f"{value:%A}, {value:%B} {value.day}"


def spoken_text(value: str) -> str:
    """Strip markdown, emoji, and link noise that TTS engines read out awkwardly."""

    value = re.sub(r"<https?://[^>]+>|https?://\S+", "", value)
    value = value.replace("*", "").replace("|", " ")
    value = "".join(char for char in value if not is_emoji_or_symbol(char))
    value = re.sub(r"\s+", " ", value)
    return value.replace(" ,", ",").replace(" .", ".").strip()


def is_emoji_or_symbol(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
        or codepoint == 0xFE0F
    )
