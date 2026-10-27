from __future__ import annotations

from pathlib import Path

import pytest
from hearletter_shared.config import parse_briefing_config
from hearletter_shared.story_parsers import parse_rundown, parse_tldr, strip_invisible

FIXTURES = Path(__file__).parent / "fixtures"
TLDR_TEXT = (FIXTURES / "tldr_2026-10-20.txt").read_text()
RUNDOWN_TEXT = (FIXTURES / "rundown_2026-10-20.txt").read_text()
INVISIBLE = ("​", "‌", "͏", "­", "﻿")


def test_tldr_reads_titled_stories_in_order_and_skips_sponsors() -> None:
    stories = parse_tldr(TLDR_TEXT)

    assert [story.title for story in stories[:3]] == [
        "Apple Announces Oct. 28 Event for Touch-Screen MacBook Pro",
        "Waymo plans to quintuple its fleet",
        "AI Proved the Quasi-Riemann Hypothesis. What Happens Now?",
    ]
    assert len(stories) == 14
    titles = " ".join(story.title for story in stories)
    for sponsor in ("CI is dead", "training GPUs", "Granola", "Reach 8 million"):
        assert sponsor not in titles
    assert stories[0].section == "Big Tech & Startups"
    assert stories[0].text.startswith("Apple will be holding a product launch event")


def test_tldr_merges_a_title_wrapped_across_lines() -> None:
    titles = [story.title for story in parse_tldr(TLDR_TEXT)]

    assert "Startup taps underwater volcano as energy source in “proof point” test" in titles


def test_rundown_reads_main_stories_and_skips_sponsors_and_tutorials() -> None:
    stories = parse_rundown(RUNDOWN_TEXT)

    assert [story.title for story in stories] == [
        "Anthropic co-founder says the singularity is underway",
        "One hacker’s AI agents break into 100+ companies",
        "Inception’s Mercury 3 speeds past rivals",
    ]
    assert [story.section for story in stories] == ["ANTHROPIC", "AI & SECURITY", "INCEPTION"]
    hacker = stories[1].text
    assert hacker.startswith("Security firm NorForge revealed")
    assert "Why it matters:" in hacker  # kept inside the word cap, ahead of the details


def test_parsers_return_clean_text() -> None:
    for story in parse_tldr(TLDR_TEXT) + parse_rundown(RUNDOWN_TEXT):
        assert not any(char in story.text or char in story.title for char in INVISIBLE)
        assert "*" not in story.text and "http" not in story.text


def test_unrecognised_layout_returns_nothing() -> None:
    assert parse_rundown(TLDR_TEXT) == []
    assert parse_tldr("Plain newsletter with no bold titles.\n\nJust prose.") == []


def test_strip_invisible_removes_preheader_filler() -> None:
    assert strip_invisible("PLUS:‌ ‌͏ news­") == "PLUS: news"


def test_config_rejects_unknown_parser() -> None:
    source = {"email": "dan@tldrnewsletter.com", "name": "TLDR", "parser": "tldr"}
    assert parse_briefing_config({"sources": [source]}).sources[0].parser == "tldr"
    with pytest.raises(ValueError, match="Unknown story parser"):
        parse_briefing_config({"sources": [{**source, "parser": "morning-brew"}]})


DEW_TEXT = (FIXTURES / "dew_2026-10-25.txt").read_text()


def test_divider_sections_read_every_item_and_skip_masthead_sponsor_and_footer() -> None:
    from hearletter_shared.story_parsers import parse_divider_sections

    stories = parse_divider_sections(DEW_TEXT)

    assert [story.title for story in stories[:2]] == [
        "Pinterest: Metrics Board: Building an Agent-ready Metrics Layer",
        "Chenyu Zhang: The Semantic Layer Race: Snowflake, Databricks, Fabric IQ, and Apache Ossie",
    ]
    assert stories[-1].title.startswith("Meesho: Kafka at Meesho")
    assert len(stories) == 8
    titles = " ".join(story.title for story in stories)
    for skipped in ("Editor", "Event Alert", "NEXT QUERY", "All rights reserved"):
        assert skipped not in titles


def test_divider_sections_keep_summaries_and_drop_link_lines_and_captions() -> None:
    from hearletter_shared.story_parsers import parse_divider_sections

    by_title = {story.title.split(":")[0]: story.text for story in parse_divider_sections(DEW_TEXT)}

    assert by_title["Pinterest"].startswith("Pinterest writes about the challenges")
    assert "Vega-Lite and Mosaic" in by_title["Microsoft"]  # inline links keep their words
    assert "Figure 1" not in by_title["LinkedIn"]  # image caption dropped
    for text in by_title.values():
        assert "http" not in text and "*" not in text


def test_divider_parser_ignores_newsletters_without_dividers() -> None:
    from hearletter_shared.story_parsers import parse_divider_sections

    assert parse_divider_sections(TLDR_TEXT) == []
