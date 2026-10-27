from __future__ import annotations

from typing import Any

import pytest
from hearletter_shared.config import parse_briefing_config

SOURCE = {"email": "crew@morningbrew.com", "name": "Morning Brew"}


def test_flush_at_must_be_local_hh_mm() -> None:
    assert parse_briefing_config({"sources": [SOURCE], "flush_at": "07:30"}).flush_at == "07:30"
    with pytest.raises(ValueError, match="flush_at"):
        parse_briefing_config({"sources": [SOURCE], "flush_at": "8am"})


def test_alias_cannot_belong_to_two_sources() -> None:
    raw: dict[str, Any] = {
        "sources": [
            {**SOURCE, "aliases": ["shared@esp.com"]},
            {"email": "dan@tldrnewsletter.com", "name": "TLDR", "aliases": ["SHARED@esp.com"]},
        ]
    }
    with pytest.raises(ValueError, match="duplicate"):
        parse_briefing_config(raw)


def test_fingerprint_ignores_order_and_delivery_but_not_content() -> None:
    from hearletter_domain.briefing import StagedNewsletter, Story, briefing_fingerprint

    def staged(source: str, raw_key: str, subject: str = "Issue") -> StagedNewsletter:
        return StagedNewsletter(
            forwarder="r@example.com",
            episode_date="2026-10-12",
            source_email=source,
            source_name=source,
            subject=subject,
            received_at=f"2026-10-12T{raw_key}",
            raw_email_key=raw_key,
            clean_word_count=1,
            stories=(Story(title="t", summary="s"),),
        )

    first = [staged("a@x.com", "k1"), staged("b@y.com", "k2")]
    resent = [staged("b@y.com", "k9"), staged("a@x.com", "k8")]
    changed = [staged("a@x.com", "k1", subject="Correction"), staged("b@y.com", "k2")]

    assert briefing_fingerprint(first) == briefing_fingerprint(resent)
    assert briefing_fingerprint(first) != briefing_fingerprint(changed)


def test_editorial_validation() -> None:
    topic = {"name": "Tech", "weight": 0}
    with pytest.raises(ValueError, match="weight above 0"):
        parse_briefing_config({"sources": [SOURCE], "editorial": {"topics": [topic]}})
    with pytest.raises(ValueError, match="target_minutes"):
        parse_briefing_config({"sources": [SOURCE], "editorial": {"target_minutes": 90}})
    defaults = parse_briefing_config({"sources": [SOURCE]}).editorial
    assert defaults.target_words == 1050 and defaults.topics == ()


def test_optional_sources_parse_and_do_not_count_toward_readiness() -> None:
    config = parse_briefing_config(
        {
            "sources": [
                SOURCE,
                {
                    "email": "weekly@example.com",
                    "name": "Weekly",
                    "required": False,
                    "carry_over_days": 5,
                    "max_stories": 2,
                },
            ]
        }
    )

    assert [s.name for s in config.required_sources] == ["Morning Brew"]
    assert [s.name for s in config.optional_sources] == ["Weekly"]
    assert config.required_source_count == 1
    assert config.carry_over_window_days == 5
    assert config.stories_to_stage_for(config.optional_sources[0]) == 2


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"required": False}, "at least one required source"),
        ({"carry_over_days": 0}, "carry_over_days"),
        ({"max_stories": 0}, "max_stories"),
        ({"required": "no"}, "required must be true or false"),
    ],
)
def test_source_validation(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_briefing_config({"sources": [{**SOURCE, **override}]})
