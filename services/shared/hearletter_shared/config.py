"""Loading the briefing source configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hearletter_domain.briefing import BriefingConfig, NewsletterSource

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

    sources = tuple(
        NewsletterSource(email=str(item["email"]).strip().lower(), name=str(item["name"]))
        for item in raw.get("sources", [])
    )
    if len({source.email for source in sources}) != len(sources):
        raise ValueError("Briefing config has duplicate source emails")

    min_sources = raw.get("min_sources")
    return BriefingConfig(
        sources=sources,
        timezone=str(raw.get("timezone", "UTC")),
        title_template=str(raw.get("title_template", "Morning Briefing - {date}")),
        max_stories_per_source=int(raw.get("max_stories_per_source", 3)),
        min_sources=int(min_sources) if min_sources is not None else None,
        allowed_forwarders=tuple(
            str(email).strip().lower() for email in raw.get("allowed_forwarders", [])
        ),
    )
