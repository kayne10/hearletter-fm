"""Builders for synthetic forwarded newsletters used by briefing tests."""

from __future__ import annotations

import json
from email.message import EmailMessage
from typing import Any

FORWARDER = "reader@example.com"

STORY_PARAGRAPH = (
    "{topic} announced a major update on Monday, according to people familiar with the plan, "
    "and analysts said the move could reshape how the industry competes over the next year. "
    "The company said the rollout starts this week and will reach most customers by summer."
)


def newsletter_body(name: str, topics: list[str]) -> str:
    sections = [
        f"{topic} makes its move\n\n{STORY_PARAGRAPH.format(topic=topic)}" for topic in topics
    ]
    filler = " ".join(["Readers also asked about markets, chips, and the week ahead."] * 4)
    return f"Welcome to {name}.\n\n" + "\n\n".join(sections) + f"\n\n{filler}\n\nUnsubscribe here"


def forwarded_email(
    *,
    source_email: str,
    source_name: str,
    subject: str,
    topics: list[str],
    forwarder: str = FORWARDER,
    date: str = "Mon, 12 Oct 2026 07:15:00 -0600",
    body: str | None = None,
) -> bytes:
    """A Gmail-style manual forward."""

    message = EmailMessage()
    message["From"] = f"Reader <{forwarder}>"
    message["To"] = "listen@beta.hearletter.fm"
    message["Subject"] = f"Fwd: {subject}"
    message["Date"] = date
    message.set_content(
        "Sent from my phone\n\n"
        "---------- Forwarded message ---------\n"
        f"From: {source_name} <{source_email}>\n"
        "Date: Mon, Oct 12, 2026 at 5:00 AM\n"
        f"Subject: {subject}\n"
        f"To: <{forwarder}>\n\n\n" + (body or newsletter_body(source_name, topics))
    )
    return message.as_bytes()


def sqs_record(message_id: str, key: str, bucket: str = "raw") -> dict[str, Any]:
    body = {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "eventTime": "2026-10-12T13:15:00.000Z",
                "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
            }
        ]
    }
    return {"messageId": message_id, "body": json.dumps(body)}


TLDR = forwarded_email(
    source_email="dan@tldrnewsletter.com",
    source_name="TLDR",
    subject="TLDR 2026-10-12",
    topics=["Nvidia", "Stripe"],
)
MORNING_BREW = forwarded_email(
    source_email="crew@morningbrew.com",
    source_name="Morning Brew",
    subject="Coffee and markets",
    topics=["Starbucks", "Delta"],
)
RUNDOWN = forwarded_email(
    source_email="news@daily.therundown.ai",
    source_name="The Rundown",
    subject="The Rundown AI",
    topics=["Anthropic", "OpenAI"],
)
UNCONFIGURED = forwarded_email(
    source_email="hello@randomnews.com",
    source_name="Random News",
    subject="Random",
    topics=["Acme"],
)
