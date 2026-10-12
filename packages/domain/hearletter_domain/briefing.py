"""Domain models for the multi-newsletter morning briefing."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def slugify(value: str) -> str:
    """Return an S3-key-safe slug (lowercase, no '@' or spaces)."""

    return _SLUG_RE.sub("_", value.strip().lower()).strip("_")


@dataclass(frozen=True, slots=True)
class NewsletterSource:
    """A newsletter sender that is allowed into the morning briefing."""

    email: str
    name: str

    @property
    def slug(self) -> str:
        return slugify(self.email)


@dataclass(frozen=True, slots=True)
class BriefingConfig:
    """Which newsletters make up one briefing and how it is assembled."""

    sources: tuple[NewsletterSource, ...]
    timezone: str = "UTC"
    title_template: str = "Morning Briefing - {date}"
    max_stories_per_source: int = 3
    min_sources: int | None = None
    allowed_forwarders: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("Briefing config must define at least one source")
        if self.min_sources is not None and not 1 <= self.min_sources <= len(self.sources):
            raise ValueError("min_sources must be between 1 and the number of sources")

    @property
    def required_source_count(self) -> int:
        """Number of distinct sources needed before a briefing is built."""

        return self.min_sources or len(self.sources)

    def source_for(self, email: str | None) -> NewsletterSource | None:
        """Return the configured source for a sender address, if any."""

        if not email:
            return None
        normalized = email.strip().lower()
        return next((source for source in self.sources if source.email == normalized), None)

    def is_forwarder_allowed(self, email: str) -> bool:
        """An empty allow-list accepts any forwarder."""

        return not self.allowed_forwarders or email.strip().lower() in self.allowed_forwarders

    def title_for(self, episode_date: str) -> str:
        return self.title_template.format(date=episode_date)


@dataclass(frozen=True, slots=True)
class ForwardedNewsletter:
    """A newsletter recovered from a forwarded (or auto-forwarded) raw email."""

    forwarder: str
    source_email: str | None
    subject: str
    received_at: str
    text: str | None
    html: str | None


@dataclass(frozen=True, slots=True)
class Story:
    """A single story pulled out of a cleaned newsletter."""

    title: str
    summary: str
    source_text: str = ""  # fuller cleaned excerpt, used by the AI script writer


@dataclass(frozen=True, slots=True)
class StagedNewsletter:
    """A cleaned newsletter parked in S3 until the day's briefing is complete."""

    forwarder: str
    episode_date: str
    source_email: str
    source_name: str
    subject: str
    received_at: str
    raw_email_key: str
    clean_word_count: int
    stories: tuple[Story, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StagedNewsletter:
        stories = tuple(Story(**story) for story in value.get("stories", []))
        return cls(**{**value, "stories": stories})


@dataclass(frozen=True, slots=True)
class BriefingScript:
    """The single spoken script for one listener's morning episode."""

    episode_id: str
    title: str
    episode_date: str
    forwarder: str
    sources: tuple[str, ...]
    text: str
    created_at: str
    writer: str = "template"  # "template" or the Claude model id that wrote the text
    template_text: str | None = None  # template draft kept for comparison when AI-written

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingScript:
        return cls(**{**value, "sources": tuple(value.get("sources", []))})


def episode_id_for(forwarder: str, episode_date: str) -> str:
    """Deterministic episode id so retries never create a second episode."""

    digest = hashlib.sha256(f"{forwarder.lower()}|{episode_date}".encode()).hexdigest()[:12]
    return f"ep_{episode_date.replace('-', '')}_{digest}"
