from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from briefing_fixtures import FORWARDER, TLDR
from hearletter_shared.forwarded_email import find_forwarded_block, parse_forwarded_email

FIXTURE = Path(__file__).parent / "fixtures" / "raw_email"


def test_parses_real_gmail_forward_of_morning_brew() -> None:
    newsletter = parse_forwarded_email(FIXTURE.read_bytes(), fallback_received_at="unused")

    assert newsletter.forwarder == "tkayne10@gmail.com"
    assert newsletter.source_email == "crew@morningbrew.com"
    assert newsletter.source_display_name == "Morning Brew"
    assert newsletter.subject == "☕ Apple of discord"
    assert newsletter.received_at == "2026-07-13T21:29:46Z"
    assert newsletter.text is not None
    assert "Forwarded message" not in newsletter.text


def test_strips_forwarder_note_and_headers_from_body() -> None:
    newsletter = parse_forwarded_email(TLDR, fallback_received_at="unused")

    assert newsletter.forwarder == FORWARDER
    assert newsletter.source_email == "dan@tldrnewsletter.com"
    assert newsletter.subject == "TLDR 2026-10-12"
    assert newsletter.text is not None
    assert newsletter.text.startswith("Welcome to TLDR.")


def test_apple_mail_forward_marker() -> None:
    block = find_forwarded_block(
        "FYI\n\nBegin forwarded message:\n\n"
        "From: The Rundown <news@daily.therundown.ai>\nSubject: AI today\n"
        "Date: October 12, 2026\n\nBody starts here."
    )

    assert block is not None
    assert block.headers["from"] == "The Rundown <news@daily.therundown.ai>"
    assert block.body == "Body starts here."


def test_auto_forward_keeps_original_sender() -> None:
    message = EmailMessage()
    message["From"] = "Morning Brew <crew@morningbrew.com>"
    message["Subject"] = "Coffee"
    message["X-Forwarded-For"] = f"{FORWARDER} listen@beta.hearletter.fm"
    message.set_content("No forward marker here.")

    newsletter = parse_forwarded_email(message.as_bytes(), fallback_received_at="2026-10-12T00:00Z")

    assert newsletter.source_email == "crew@morningbrew.com"
    assert newsletter.forwarder == FORWARDER
    assert newsletter.received_at == "2026-10-12T00:00Z"
