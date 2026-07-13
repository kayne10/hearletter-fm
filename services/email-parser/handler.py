"""Lambda handler for parsing SES inbound emails stored in S3."""

from __future__ import annotations

import json
import os
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
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import log_lambda_event

DEFAULT_TENANT_ID = "default"
DEFAULT_RAW_EMAIL_PREFIX = "raw-email/default/"


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Parse an SES receipt event and enqueue a parsed-newsletter event."""

    import boto3

    log_lambda_event("email-parser", event)

    s3_client = boto3.client("s3")
    sqs_client = boto3.client("sqs")

    parsed_event = parse_ses_event(
        event,
        env=os.environ,
        s3_client=s3_client,
    )

    queue_url = os.environ.get("PARSED_QUEUE_URL")
    if queue_url:
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=dumps_event(parsed_event))

    return parsed_event.to_dict()


def parse_ses_event(
    event: dict[str, Any],
    *,
    env: dict[str, str],
    s3_client: Any,
) -> EventEnvelope[ParsedNewsletterPayload]:
    """Build a parsed-newsletter event from an SES Lambda event."""

    record = event["Records"][0]
    ses_mail = record["ses"]["mail"]

    message_id = str(ses_mail["messageId"])
    tenant_id = env.get("TENANT_ID", DEFAULT_TENANT_ID)
    newsletter_id = new_id("nws")
    now = utc_now_iso()
    raw_email = resolve_raw_email_ref(record, env=env)
    raw_message = get_s3_object_bytes(s3_client, raw_email)
    bodies = parse_mime(raw_message)
    html_ref, text_ref = write_body_artifacts(
        s3_client,
        artifact_bucket=required_env(env, "ARTIFACT_BUCKET"),
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        bodies=bodies,
    )

    payload = ParsedNewsletterPayload(
        raw_email=raw_email,
        sender=str(ses_mail["source"]),
        recipient=str(ses_mail["destination"][0]),
        subject=str(ses_mail.get("commonHeaders", {}).get("subject", "Untitled newsletter")),
        received_at=now,
        html=html_ref,
        text=text_ref,
    )
    return EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.parsed",
        schema_version="1.0",
        correlation_id=message_id,
        tenant_id=tenant_id,
        newsletter_id=newsletter_id,
        occurred_at=now,
        payload=payload,
    )


def resolve_raw_email_ref(record: dict[str, Any], *, env: dict[str, str]) -> S3ObjectRef:
    """Resolve the S3 location of the raw SES email.

    When SES invokes Lambda after an S3 receipt action, `receipt.action` describes the Lambda
    action, not the previous S3 action. In that case SES stores the object as
    `<object_key_prefix><message_id>`.
    """

    ses_mail = record["ses"]["mail"]
    receipt_action = record["ses"]["receipt"].get("action", {})
    bucket_name = receipt_action.get("bucketName")
    object_key = receipt_action.get("objectKey")

    if bucket_name and object_key:
        return S3ObjectRef(bucket=str(bucket_name), key=str(object_key))

    raw_bucket = required_env(env, "RAW_EMAIL_BUCKET")
    raw_prefix = env.get("RAW_EMAIL_PREFIX", DEFAULT_RAW_EMAIL_PREFIX)
    normalized_prefix = raw_prefix if raw_prefix.endswith("/") else f"{raw_prefix}/"
    return S3ObjectRef(bucket=raw_bucket, key=f"{normalized_prefix}{ses_mail['messageId']}")


def get_s3_object_bytes(s3_client: Any, ref: S3ObjectRef) -> bytes:
    """Fetch an S3 object as bytes."""

    response = s3_client.get_object(Bucket=ref.bucket, Key=ref.key)
    body = response["Body"]
    return bytes(body.read())


def write_body_artifacts(
    s3_client: Any,
    *,
    artifact_bucket: str,
    tenant_id: str,
    newsletter_id: str,
    bodies: dict[str, str | None],
) -> tuple[S3ObjectRef | None, S3ObjectRef | None]:
    """Persist parsed text/html bodies and return their S3 references."""

    html_ref = put_text_artifact(
        s3_client,
        bucket=artifact_bucket,
        key=f"parsed/{tenant_id}/{newsletter_id}/body.html",
        body=bodies.get("html"),
        content_type="text/html; charset=utf-8",
    )
    text_ref = put_text_artifact(
        s3_client,
        bucket=artifact_bucket,
        key=f"parsed/{tenant_id}/{newsletter_id}/body.txt",
        body=bodies.get("text"),
        content_type="text/plain; charset=utf-8",
    )
    return html_ref, text_ref


def put_text_artifact(
    s3_client: Any,
    *,
    bucket: str,
    key: str,
    body: str | None,
    content_type: str,
) -> S3ObjectRef | None:
    """Write a text artifact to S3 if present."""

    if body is None:
        return None
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType=content_type,
    )
    return S3ObjectRef(bucket=bucket, key=key)


def required_env(env: dict[str, str], name: str) -> str:
    """Read a required environment variable with a useful error."""

    value = env.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def event_to_json(event: EventEnvelope[Any]) -> str:
    """Pretty-print an event for local debugging."""

    return json.dumps(event.to_dict(), indent=2, sort_keys=True)


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
