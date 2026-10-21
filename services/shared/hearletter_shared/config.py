"""Loading the briefing source configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hearletter_domain.briefing import (
    BriefingConfig,
    EditorialConfig,
    NewsletterSource,
    TopicWeight,
)

from hearletter_shared.story_parsers import STORY_PARSERS

CONFIG_FILE_NAME = "briefing.json"
CONFIG_PATH_ENV = "BRIEFING_CONFIG_PATH"
REPO_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / CONFIG_FILE_NAME


def default_config_path(env: Mapping[str, str] = os.environ) -> Path:
    """Resolve config: explicit env var, then the Lambda bundle root, then the repo copy."""

    if env.get(CONFIG_PATH_ENV):
        return Path(env[CONFIG_PATH_ENV])
    task_root = env.get("LAMBDA_TASK_ROOT")
    if task_root and (Path(task_root) / CONFIG_FILE_NAME).exists():
        return Path(task_root) / CONFIG_FILE_NAME
    return REPO_CONFIG_PATH


def load_briefing_config(path: Path | None = None) -> BriefingConfig:
    """Read and validate the briefing config JSON file."""

    config_path = path or default_config_path()
    return parse_briefing_config(json.loads(config_path.read_text(encoding="utf-8")))


def parse_briefing_config(raw: Mapping[str, Any]) -> BriefingConfig:
    """Build a BriefingConfig from decoded JSON."""

    sources = tuple(parse_source(item) for item in raw.get("sources", []))
    addresses = [address for source in sources for address in (source.email, *source.aliases)]
    if len(set(addresses)) != len(addresses):
        raise ValueError("Briefing config has duplicate source emails or aliases")

    min_sources = raw.get("min_sources")
    return BriefingConfig(
        sources=sources,
        timezone=str(raw.get("timezone", "UTC")),
        title_template=str(raw.get("title_template", "Morning Briefing - {date}")),
        max_stories_per_source=int(raw.get("max_stories_per_source", 3)),
        min_sources=int(min_sources) if min_sources is not None else None,
        allowed_forwarders=lowered(raw.get("allowed_forwarders", [])),
        flush_at=str(raw["flush_at"]) if raw.get("flush_at") else None,
        editorial=parse_editorial(raw.get("editorial") or {}),
    )


def parse_editorial(raw: Mapping[str, Any]) -> EditorialConfig:
    defaults = EditorialConfig()
    return EditorialConfig(
        target_minutes=float(raw.get("target_minutes", defaults.target_minutes)),
        words_per_minute=int(raw.get("words_per_minute", defaults.words_per_minute)),
        candidates_per_source=int(
            raw.get("candidates_per_source", defaults.candidates_per_source)
        ),
        topics=tuple(
            TopicWeight(
                name=str(topic["name"]).strip(),
                weight=float(topic["weight"]),
                examples=str(topic.get("examples", "")).strip(),
            )
            for topic in raw.get("topics", [])
        ),
        rules=tuple(str(rule).strip() for rule in raw.get("rules", []) if str(rule).strip()),
    )


def parse_source(item: Mapping[str, Any]) -> NewsletterSource:
    return NewsletterSource(
        email=str(item["email"]).strip().lower(),
        name=str(item["name"]),
        aliases=lowered(item.get("aliases", [])),
        domains=lowered(item.get("domains", [])),
        sender_names=lowered(item.get("sender_names", [])),
        parser=parse_parser_name(item.get("parser")),
    )


def parse_parser_name(value: Any) -> str | None:
    if value is None:
        return None
    name = str(value).strip().lower()
    if name not in STORY_PARSERS:
        raise ValueError(f"Unknown story parser {name!r}; choose from {sorted(STORY_PARSERS)}")
    return name


def lowered(values: Any) -> tuple[str, ...]:
    return tuple(str(value).strip().lower() for value in values)
