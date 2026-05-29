"""JSON-serializable contracts exchanged between pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import uuid4

from hearletter_domain.models import ContentMode, S3ObjectRef

PayloadT = TypeVar("PayloadT")

SchemaVersion = Literal["1.0"]
EventType = Literal[
    "newsletter.parsed",
    "newsletter.cleaned",
    "briefing.scripted",
    "episode.generated",
]


def utc_now_iso() -> str:
    """Return an AWS-friendly UTC timestamp."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """Create a compact unique identifier with a readable prefix."""

    return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class EventEnvelope(Generic[PayloadT]):
    """Common metadata carried by every async pipeline event."""

    event_id: str
    event_type: EventType
    schema_version: SchemaVersion
    correlation_id: str
    tenant_id: str
    newsletter_id: str
    occurred_at: str
    payload: PayloadT

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event into a JSON-compatible dictionary."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class ParsedNewsletterPayload:
    """Output of the email-parser stage."""

    raw_email: S3ObjectRef
    sender: str
    recipient: str
    subject: str
    received_at: str
    html: S3ObjectRef | None
    text: S3ObjectRef | None


@dataclass(frozen=True, slots=True)
class CleanedNewsletterPayload:
    """Output of the newsletter-cleaner stage."""

    title: str
    source: str | None
    clean_text: S3ObjectRef
    removed_sections: list[str]
    word_count: int


@dataclass(frozen=True, slots=True)
class BriefingScriptPayload:
    """Output of the summarizer stage."""

    mode: ContentMode
    title: str
    script: S3ObjectRef
    estimated_duration_seconds: int | None
    voice: str


@dataclass(frozen=True, slots=True)
class GeneratedEpisodePayload:
    """Output of the TTS stage."""

    episode_id: str
    title: str
    audio: S3ObjectRef
    audio_url: str
    mime_type: str
    byte_length: int
    duration_seconds: int | None
    published_at: str

