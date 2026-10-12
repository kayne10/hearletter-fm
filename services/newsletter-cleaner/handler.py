# LEGACY: not used by the briefing pipeline. Safe to delete; see docs/briefing-pipeline.md.
"""Lambda handler for cleaning parsed newsletter content.

LEGACY: superseded by services/briefing-builder. Cleaning logic lives in
hearletter_shared.cleaning.
"""

from __future__ import annotations

import json
import os
from typing import Any

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import CleanedNewsletterPayload, EventEnvelope, new_id, utc_now_iso
from hearletter_shared.cleaning import (  # noqa: F401 - re-exported for legacy callers
    agenda_paragraph_score,
    cap_words,
    clean_excerpt,
    clean_html,
    clean_newsletter_content,
    clean_plain_text,
    extract_agenda_items,
    extract_agenda_story_candidates,
    extract_story_candidates,
    is_image_credit,
    is_low_value_paragraph,
    is_low_value_title,
    looks_like_heading,
    matching_agenda_item,
    merge_story_paragraphs,
    normalize_line,
    previous_title,
    should_start_new_paragraph,
    split_at_next_story_marker,
    story_score,
    token_overlap,
    unwrap_plain_text_lines,
)
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Clean parsed newsletter text and emit a cleaned-newsletter contract."""

    import boto3

    log_lambda_event("newsletter-cleaner", event)
    s3_client = boto3.client("s3")
    sqs_client = boto3.client("sqs")
    output_events = [
        process_event(
            pipeline_event,
            s3_client=s3_client,
            artifact_bucket=os.environ.get("ARTIFACT_BUCKET"),
        )
        for pipeline_event in iter_pipeline_events(event)
    ]

    queue_url = os.environ.get("CLEANED_QUEUE_URL")
    if queue_url:
        for output_event in output_events:
            sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(output_event))

    return {
        "processed": len(output_events),
        "events": [output_event.to_dict() for output_event in output_events],
    }


def process_event(
    event: dict[str, Any],
    *,
    s3_client: Any | None = None,
    artifact_bucket: str | None = None,
) -> EventEnvelope[CleanedNewsletterPayload]:
    """Clean a single parsed-newsletter pipeline event."""

    now = utc_now_iso()
    tenant_id = str(event["tenant_id"])
    newsletter_id = str(event["newsletter_id"])
    payload = event["payload"]
    subject = str(payload.get("subject", "Untitled newsletter"))

    clean_key = f"cleaned/{tenant_id}/{newsletter_id}/content.txt"
    stories_key = f"cleaned/{tenant_id}/{newsletter_id}/story_candidates.json"
    removed_sections: list[str] = []
    word_count = 0

    if s3_client is not None and artifact_bucket:
        html = read_optional_text_ref(s3_client, payload.get("html"))
        text = read_optional_text_ref(s3_client, payload.get("text"))
        clean_text, removed_sections = clean_newsletter_content(html=html, text=text)
        story_candidates = extract_story_candidates(clean_text)
        word_count = len(clean_text.split())
        put_text(
            s3_client,
            bucket=artifact_bucket,
            key=clean_key,
            body=clean_text,
            content_type="text/plain; charset=utf-8",
        )
        put_text(
            s3_client,
            bucket=artifact_bucket,
            key=stories_key,
            body=json.dumps(story_candidates, indent=2, sort_keys=True),
            content_type="application/json; charset=utf-8",
        )
    else:
        artifact_bucket = "ARTIFACT_BUCKET"

    clean_payload = CleanedNewsletterPayload(
        title=subject,
        source=str(payload.get("sender")) if payload.get("sender") else None,
        clean_text=S3ObjectRef(bucket=artifact_bucket, key=clean_key),
        removed_sections=removed_sections,
        word_count=word_count,
        notification_email=payload.get("notification_email"),
    )

    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.cleaned",
        schema_version="1.0",
        correlation_id=str(event["correlation_id"]),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=clean_payload,
    )


def read_optional_text_ref(s3_client: Any, ref: Any) -> str | None:
    """Read a text artifact from an S3 ref dictionary if present."""

    if not ref:
        return None
    bucket = str(ref["bucket"])
    key = str(ref["key"])
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read().decode("utf-8")


def put_text(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    body: str,
    content_type: str,
) -> None:
    """Write a UTF-8 text artifact to S3."""

    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType=content_type,
    )

