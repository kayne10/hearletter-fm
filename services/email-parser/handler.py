"""Lambda handler for parsing SES inbound emails stored in S3."""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from typing import Any

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import (
    EventEnvelope,
    ParsedNewsletterPayload,
    new_id,
    utc_now_iso,
)


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Parse an SES receipt event into the first pipeline contract.

    This skeleton intentionally keeps AWS IO behind future storage helpers. The expected SES event
    contains S3 bucket/key metadata from the receipt action.
    """

    record = event["Records"][0]
    ses_mail = record["ses"]["mail"]
    receipt_action = record["ses"]["receipt"]["action"]

    message_id = str(ses_mail["messageId"])
    tenant_id = "default"
    newsletter_id = new_id("nws")
    now = utc_now_iso()

    payload = ParsedNewsletterPayload(
        raw_email=S3ObjectRef(
            bucket=str(receipt_action["bucketName"]),
            key=str(receipt_action["objectKey"]),
        ),
        sender=str(ses_mail["source"]),
        recipient=str(ses_mail["destination"][0]),
        subject=str(ses_mail.get("commonHeaders", {}).get("subject", "Untitled newsletter")),
        received_at=now,
        html=None,
        text=None,
    )
    parsed_event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.parsed",
        schema_version="1.0",
        correlation_id=message_id,
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=payload,
    )

    return parsed_event.to_dict()


def parse_mime(raw_message: bytes) -> dict[str, str | None]:
    """Extract useful bodies from a raw MIME email."""

    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    html_body: str | None = None
    text_body: str | None = None

    for part in message.walk():
        content_type = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        if content_type == "text/html" and html_body is None:
            html_body = str(part.get_content())
        if content_type == "text/plain" and text_body is None:
            text_body = str(part.get_content())

    if not message.is_multipart():
        content_type = message.get_content_type()
        if content_type == "text/html":
            html_body = str(message.get_content())
        if content_type == "text/plain":
            text_body = str(message.get_content())

    return {"html": html_body, "text": text_body}

