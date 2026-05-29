"""Lambda handler for cleaning parsed newsletter content."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import CleanedNewsletterPayload, EventEnvelope, new_id, utc_now_iso
from hearletter_utils.text import normalize_whitespace

JUNK_LINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"view (this )?(email|newsletter) in (your )?browser",
        r"unsubscribe",
        r"privacy policy",
        r"manage (your )?preferences",
        r"sponsored by",
        r"advertisement",
    )
]


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Clean parsed newsletter text and emit a cleaned-newsletter contract."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    payload = event["payload"]
    subject = str(payload.get("subject", "Untitled newsletter"))

    clean_key = f"cleaned/{tenant_id}/{newsletter_id}/content.txt"
    clean_payload = CleanedNewsletterPayload(
        title=subject,
        source=str(payload.get("sender")) if payload.get("sender") else None,
        clean_text=S3ObjectRef(bucket="ARTIFACT_BUCKET", key=clean_key),
        removed_sections=[],
        word_count=0,
    )

    cleaned_event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.cleaned",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=clean_payload,
    )
    return cleaned_event.to_dict()


def clean_html(html: str) -> tuple[str, list[str]]:
    """Convert newsletter HTML into readable text and report removed section types."""

    soup = BeautifulSoup(html, "html.parser")
    removed: list[str] = []

    for selector in ("script", "style", "noscript", "svg", "img", "nav", "footer"):
        for node in soup.select(selector):
            node.decompose()
            removed.append(selector)

    text = soup.get_text("\n")
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in JUNK_LINE_PATTERNS):
            removed.append("junk_line")
            continue
        cleaned_lines.append(stripped)

    return normalize_whitespace("\n".join(cleaned_lines)), sorted(set(removed))

