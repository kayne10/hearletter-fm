"""Lambda handler for emailing generated episode links back to the sender."""

from __future__ import annotations

import os
from html import escape
from typing import Any

from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event

DEFAULT_URL_TTL_SECONDS = 604800
DEFAULT_SUBJECT_PREFIX = "Your Hearletter FM briefing is ready"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Send a completion email for generated episode events."""

    import boto3

    log_lambda_event("notifier", event)
    s3_client = boto3.client("s3")
    ses_client = boto3.client("ses", region_name=os.environ.get("SES_SEND_REGION"))
    from_email = required_env("NOTIFICATION_FROM_EMAIL")
    url_ttl_seconds = int(os.environ.get("AUDIO_URL_TTL_SECONDS", DEFAULT_URL_TTL_SECONDS))

    results = [
        process_event(
            pipeline_event,
            s3_client=s3_client,
            ses_client=ses_client,
            from_email=from_email,
            url_ttl_seconds=url_ttl_seconds,
        )
        for pipeline_event in iter_pipeline_events(event)
    ]
    return {"processed": len(results), "results": results}


def process_event(
    event: dict[str, Any],
    *,
    s3_client: Any,
    ses_client: Any,
    from_email: str,
    url_ttl_seconds: int = DEFAULT_URL_TTL_SECONDS,
) -> dict[str, Any]:
    """Send a single generated episode notification email."""

    payload = event["payload"]
    recipient = notification_recipient(payload)
    if recipient is None:
        return {
            "status": "skipped",
            "reason": "missing_notification_email",
            "event_id": event.get("event_id"),
            "newsletter_id": event.get("newsletter_id"),
        }

    audio_ref = payload["audio"]
    audio_url = presigned_audio_url(
        s3_client,
        bucket=str(audio_ref["bucket"]),
        key=str(audio_ref["key"]),
        expires_in=url_ttl_seconds,
    )
    title = str(payload.get("title", "Hearletter FM briefing"))
    subject = build_subject(title)
    response = ses_client.send_email(
        Source=from_email,
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {
                    "Data": build_text_body(title, audio_url, url_ttl_seconds),
                    "Charset": "UTF-8",
                },
                "Html": {
                    "Data": build_html_body(title, audio_url, url_ttl_seconds),
                    "Charset": "UTF-8",
                },
            },
        },
    )

    return {
        "status": "sent",
        "message_id": response.get("MessageId"),
        "to_email": recipient,
        "audio_bucket": str(audio_ref["bucket"]),
        "audio_key": str(audio_ref["key"]),
        "url_ttl_seconds": url_ttl_seconds,
    }


def notification_recipient(payload: dict[str, Any]) -> str | None:
    """Return the recipient address for completion notifications."""

    value = payload.get("notification_email")
    if not value:
        return None
    return str(value)


def presigned_audio_url(s3_client: Any, *, bucket: str, key: str, expires_in: int) -> str:
    """Create a private temporary link for the generated MP3."""

    return str(
        s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    )


def build_subject(title: str) -> str:
    """Create the SES email subject."""

    return f"{DEFAULT_SUBJECT_PREFIX}: {title}"


def build_text_body(title: str, audio_url: str, url_ttl_seconds: int) -> str:
    """Create a plain-text notification body."""

    return (
        f"{title}\n\n"
        "Your private Hearletter FM briefing is ready:\n"
        f"{audio_url}\n\n"
        f"This link expires in {human_ttl(url_ttl_seconds)}."
    )


def build_html_body(title: str, audio_url: str, url_ttl_seconds: int) -> str:
    """Create a simple HTML notification body."""

    safe_title = escape(title)
    safe_url = escape(audio_url, quote=True)
    return (
        "<!doctype html>"
        "<html><body>"
        f"<h1>{safe_title}</h1>"
        "<p>Your private Hearletter FM briefing is ready.</p>"
        f'<p><a href="{safe_url}">Listen to the MP3</a></p>'
        f"<p>This link expires in {escape(human_ttl(url_ttl_seconds))}.</p>"
        "</body></html>"
    )


def human_ttl(seconds: int) -> str:
    """Format link lifetime for listener-facing copy."""

    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{seconds} seconds"


def required_env(name: str) -> str:
    """Return a required environment variable."""

    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
