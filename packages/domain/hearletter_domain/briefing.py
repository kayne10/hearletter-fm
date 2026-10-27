"""Domain models for the multi-newsletter morning briefing."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
_LOCAL_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def slugify(value: str) -> str:
    """Return an S3-key-safe slug (lowercase, no '@' or spaces)."""

    return _SLUG_RE.sub("_", value.strip().lower()).strip("_")


@dataclass(frozen=True, slots=True)
class NewsletterSource:
    """A newsletter that is allowed into the morning briefing.

    `email` is the canonical id (staging keys, ordering). Newsletters change sending addresses
    (e.g. Morning Brew also sends from its email provider, mail.sailthru.com), so a source also
    matches extra addresses, sender domains (subdomains included), and From display names.
    """

    email: str
    name: str
    aliases: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    sender_names: tuple[str, ...] = ()
    parser: str | None = None  # template-aware story parser name; None uses the generic one
    # Optional sources (e.g. a weekly) never hold an episode back. An issue joins the next
    # episode built within carry_over_days of its arrival, exactly once.
    required: bool = True
    carry_over_days: int = 7
    max_stories: int | None = None  # per-source cap on staged stories; None uses the default

    @property
    def slug(self) -> str:
        return slugify(self.email)

    def matches_address(self, email: str) -> bool:
        return email == self.email or email in self.aliases

    def matches_domain(self, email: str) -> bool:
        domain = email.rpartition("@")[2]
        return any(domain == d or domain.endswith(f".{d}") for d in self.domains)

    def matches_sender_name(self, display_name: str) -> bool:
        return display_name in self.sender_names


@dataclass(frozen=True, slots=True)
class TopicWeight:
    """A topic the listener cares about; weights are relative to each other."""

    name: str
    weight: float
    examples: str = ""


@dataclass(frozen=True, slots=True)
class EditorialConfig:
    """Priorities and house rules handed to the AI script writer."""

    target_minutes: float = 7.0
    words_per_minute: int = 150
    candidates_per_source: int = 6  # stories per newsletter the writer can choose from
    topics: tuple[TopicWeight, ...] = ()
    rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.target_minutes <= 30:
            raise ValueError("editorial.target_minutes must be between 1 and 30")
        if self.candidates_per_source < 1:
            raise ValueError("editorial.candidates_per_source must be at least 1")
        if any(topic.weight <= 0 or not topic.name.strip() for topic in self.topics):
            raise ValueError("editorial.topics need a name and a weight above 0")

    @property
    def target_words(self) -> int:
        return round(self.target_minutes * self.words_per_minute)


@dataclass(frozen=True, slots=True)
class BriefingConfig:
    """Which newsletters make up one briefing and how it is assembled."""

    sources: tuple[NewsletterSource, ...]
    timezone: str = "UTC"
    title_template: str = "Morning Briefing - {date}"
    max_stories_per_source: int = 3
    min_sources: int | None = None
    allowed_forwarders: tuple[str, ...] = ()
    flush_at: str | None = None  # local HH:MM deadline to build with whatever has arrived
    editorial: EditorialConfig = field(default_factory=EditorialConfig)

    def __post_init__(self) -> None:
        required = self.required_sources
        if not required:
            raise ValueError("Briefing config must define at least one required source")
        if self.min_sources is not None and not 1 <= self.min_sources <= len(required):
            raise ValueError("min_sources must be between 1 and the number of required sources")
        for source in self.sources:
            if not 1 <= source.carry_over_days <= 30:
                raise ValueError(f"{source.email}: carry_over_days must be between 1 and 30")
            if source.max_stories is not None and source.max_stories < 1:
                raise ValueError(f"{source.email}: max_stories must be at least 1")
        if self.flush_at is not None and not _LOCAL_TIME_RE.match(self.flush_at):
            raise ValueError("flush_at must be a 24-hour local time like 08:00")

    @property
    def stories_to_stage(self) -> int:
        """Stage enough stories for the AI writer; the template still reads the first few."""

        return max(self.max_stories_per_source, self.editorial.candidates_per_source)

    def stories_to_stage_for(self, source: NewsletterSource) -> int:
        return source.max_stories or self.stories_to_stage

    @property
    def required_sources(self) -> tuple[NewsletterSource, ...]:
        return tuple(source for source in self.sources if source.required)

    @property
    def optional_sources(self) -> tuple[NewsletterSource, ...]:
        return tuple(source for source in self.sources if not source.required)

    @property
    def carry_over_window_days(self) -> int:
        """How far back to look for unused optional issues (0 when there are none)."""

        return max((source.carry_over_days for source in self.optional_sources), default=0)

    @property
    def required_source_count(self) -> int:
        """Number of distinct required sources needed before a briefing is built."""

        return self.min_sources or len(self.required_sources)

    def source_for(
        self, email: str | None, display_name: str | None = None
    ) -> NewsletterSource | None:
        """Match a sender: exact address/alias first, then domain, then display name."""

        address = (email or "").strip().lower()
        name = (display_name or "").strip().lower()
        tiers = [
            (address, NewsletterSource.matches_address),
            (address, NewsletterSource.matches_domain),
            (name, NewsletterSource.matches_sender_name),
        ]
        for value, matches in tiers:
            if value:
                found = next((source for source in self.sources if matches(source, value)), None)
                if found is not None:
                    return found
        return None

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
    source_display_name: str | None = None


@dataclass(frozen=True, slots=True)
class Story:
    """A single story pulled out of a cleaned newsletter."""

    title: str
    summary: str
    source_text: str = ""  # fuller cleaned excerpt, used by the AI script writer
    section: str = ""  # the newsletter's own section label, e.g. "Big Tech & Startups"


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
    extractor: str = "generic"  # which story parser produced `stories`

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def content_digest(self) -> str:
        """Hash of what gets read out. Ignores which forward delivered it and when."""

        content = {
            "source": self.source_email,
            "subject": self.subject,
            "stories": [asdict(story) for story in self.stories],
        }
        encoded = json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()

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
    fingerprint: str = ""  # identifies the exact set of newsletter content in this episode
    source_names: tuple[str, ...] = ()
    included_digests: tuple[str, ...] = ()  # content digest of every newsletter in the episode
    version: int = 1  # 1 for the day's first episode, 2+ when later newsletters change it
    writer: str = "template"  # "template" or the Claude model id that wrote the text
    template_text: str | None = None  # template draft kept for comparison when AI-written

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BriefingScript:
        return cls(
            **{
                **value,
                "sources": tuple(value.get("sources", [])),
                "source_names": tuple(value.get("source_names", [])),
                "included_digests": tuple(value.get("included_digests", [])),
            }
        )


def briefing_fingerprint(newsletters: Iterable[StagedNewsletter]) -> str:
    """Same newsletters (in any order, from any forward) give the same fingerprint."""

    digests = sorted(item.content_digest() for item in newsletters)
    return hashlib.sha256("|".join(digests).encode()).hexdigest()[:16]


def episode_id_for(forwarder: str, episode_date: str, fingerprint: str = "") -> str:
    """Deterministic episode id: retries and re-forwards of the same content reuse it."""

    seed = f"{forwarder.lower()}|{episode_date}|{fingerprint}"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"ep_{episode_date.replace('-', '')}_{digest}"
