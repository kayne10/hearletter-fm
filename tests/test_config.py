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
