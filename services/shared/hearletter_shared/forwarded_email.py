"""Recover the original newsletter from a forwarded raw MIME email.

Handles manual forwards (Gmail "Forwarded message", Apple Mail "Begin forwarded message",
Outlook "Original Message") and auto-forwards, where the original From header is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr

from hearletter_domain.briefing import ForwardedNewsletter

FORWARD_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"-{2,}\s*Forwarded message\s*-{2,}"  # Gmail
    r"|Begin forwarded message:"  # Apple Mail
    r"|-{2,}\s*Original Message\s*-{2,}"  # Outlook
    r")",
    re.IGNORECASE | re.MULTILINE,
)
HEADER_LINE_RE = re.compile(r"^\s*\**(From|Subject|Date|Sent|To|Cc|Reply-To)\**:\s*(.*)$", re.I)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class ForwardedBlock:
    """Headers and body found after a forward marker."""

    headers: dict[str, str]
    body: str


def parse_forwarded_email(raw_message: bytes, *, fallback_received_at: str) -> ForwardedNewsletter:
    """Parse raw MIME bytes into the newsletter that was forwarded."""

    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    if not isinstance(message, EmailMessage):  # pragma: no cover - policy.default guarantees it
        raise TypeError("Expected EmailMessage with policy.default")

    text, html = extract_bodies(message)
    outer_from = address_of(str(message.get("From", "")))
    outer_subject = str(message.get("Subject", ""))
    text_block = find_forwarded_block(text)
    block = text_block or (find_forwarded_block(html_to_text(html)) if html else None)

    if block is not None:
        source_header = block.headers.get("from", "")
        source_email = address_of(source_header)
        subject = block.headers.get("subject") or strip_forward_prefix(outer_subject)
        forwarder = outer_from
        if text_block is not None:
            text = text_block.body  # drop the forwarder's note and the forward headers
    else:
        # Auto-forwarded mail keeps the newsletter's From header.
        source_header = str(message.get("From", ""))
        source_email = outer_from
        subject = outer_subject
        forwarder = auto_forwarder(message) or outer_from

    return ForwardedNewsletter(
        forwarder=forwarder or "",
        source_email=source_email,
        subject=subject.strip() or "Untitled newsletter",
        received_at=message_received_at(message) or fallback_received_at,
        text=text,
        html=html,
        source_display_name=display_name_of(source_header),
    )


def extract_bodies(message: EmailMessage) -> tuple[str | None, str | None]:
    """Return the first non-attachment text/plain and text/html bodies."""

    text: str | None = None
    html: str | None = None
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain" and text is None:
            text = str(part.get_content())
        elif content_type == "text/html" and html is None:
            html = str(part.get_content())
    return text, html


def find_forwarded_block(text: str | None) -> ForwardedBlock | None:
    """Locate the forwarded header block and return its headers and the body below it."""

    if not text:
        return None
    match = FORWARD_MARKER_RE.search(text)
    if match is None:
        return None

    lines = text[match.end() :].splitlines()
    headers: dict[str, str] = {}
    body_start = len(lines)
    for position, line in enumerate(lines):
        header = HEADER_LINE_RE.match(line)
        if header:
            headers.setdefault(header.group(1).lower(), header.group(2).strip())
            continue
        if not headers and not line.strip():
            continue  # blank lines between the marker and the first header
        body_start = position
        break
    if "from" not in headers:
        return None
    return ForwardedBlock(headers=headers, body="\n".join(lines[body_start:]).strip())


def address_of(value: str) -> str | None:
    """Return the lowercase email address from a header-like value."""

    _, address = parseaddr(value)
    return address.strip().lower() or None


def display_name_of(value: str) -> str | None:
    """Return the display name from a header-like value ("Morning Brew <x@y>")."""

    name, _ = parseaddr(value)
    return name.strip().strip('"').strip() or None


def auto_forwarder(message: EmailMessage) -> str | None:
    """Gmail auto-forwarding sets X-Forwarded-For: <forwarder> <destination>."""

    header = message.get("X-Forwarded-For")
    if not header:
        return None
    first = str(header).replace(",", " ").split()
    return address_of(first[0]) if first else None


def message_received_at(message: EmailMessage) -> str | None:
    """Return the outer Date header as an ISO-8601 UTC timestamp."""

    date_header = message.get("Date")
    moment = getattr(date_header, "datetime", None)
    if not isinstance(moment, datetime):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def strip_forward_prefix(subject: str) -> str:
    return re.sub(r"^\s*(fwd?|fw)\s*:\s*", "", subject, flags=re.IGNORECASE)


def html_to_text(html: str) -> str:
    """Cheap HTML-to-text good enough for finding forward headers."""

    with_breaks = re.sub(r"(?i)<br\s*/?>|</(div|p|tr|li)>", "\n", html)
    return "\n".join(
        line.strip() for line in TAG_RE.sub("", with_breaks).splitlines()
    ).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

