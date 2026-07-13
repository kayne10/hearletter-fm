"""Helpers for Lambda event logging and SQS unwrapping."""

from __future__ import annotations

import json
import os
from typing import Any

from hearletter_shared.event_codec import loads_event

DEFAULT_LOG_CHARS = 8000


def log_lambda_event(stage: str, event: dict[str, Any]) -> None:
    """Log a compact, structured event summary for CloudWatch troubleshooting."""

    print(
        json.dumps(
            {
                "message": "lambda_event_received",
                "stage": stage,
                "summary": summarize_lambda_event(event),
            },
            sort_keys=True,
        )
    )

    if os.environ.get("LOG_FULL_EVENTS", "").lower() == "true":
        print(
            json.dumps(
                {
                    "message": "lambda_event_full",
                    "stage": stage,
                    "event": truncate_value(event, max_chars=DEFAULT_LOG_CHARS),
                },
                default=str,
                sort_keys=True,
            )
        )


def summarize_lambda_event(event: dict[str, Any]) -> dict[str, Any]:
    """Summarize common AWS Lambda event shapes without dumping large payloads."""

    records = event.get("Records")
    if isinstance(records, list):
        summaries = [summarize_record(record) for record in records[:10] if isinstance(record, dict)]
        return {
            "shape": "records",
            "record_count": len(records),
            "records": summaries,
        }

    return {
        "shape": "direct",
        "keys": sorted(event.keys()),
        "event_type": event.get("event_type"),
        "event_id": event.get("event_id"),
        "correlation_id": event.get("correlation_id"),
        "tenant_id": event.get("tenant_id"),
        "newsletter_id": event.get("newsletter_id"),
    }


def summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Summarize an AWS event record."""

    if "ses" in record:
        mail = record.get("ses", {}).get("mail", {})
        receipt = record.get("ses", {}).get("receipt", {})
        return {
            "source": "ses",
            "message_id": mail.get("messageId"),
            "mail_source": mail.get("source"),
            "destination": mail.get("destination"),
            "receipt_action_type": receipt.get("action", {}).get("type"),
        }

    if "body" in record:
        body = str(record.get("body", ""))
        decoded = decode_json_object(body)
        return {
            "source": "sqs",
            "message_id": record.get("messageId"),
            "event_type": decoded.get("event_type") if decoded else None,
            "event_id": decoded.get("event_id") if decoded else None,
            "correlation_id": decoded.get("correlation_id") if decoded else None,
            "tenant_id": decoded.get("tenant_id") if decoded else None,
            "newsletter_id": decoded.get("newsletter_id") if decoded else None,
            "body_chars": len(body),
        }

    return {
        "source": record.get("eventSource") or record.get("EventSource") or "unknown",
        "keys": sorted(record.keys()),
    }


def iter_pipeline_events(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return pipeline event dictionaries from either direct or SQS Lambda events."""

    records = event.get("Records")
    if not isinstance(records, list):
        return [event]

    pipeline_events: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or "body" not in record:
            continue
        pipeline_events.append(loads_event(str(record["body"])))
    return pipeline_events


def decode_json_object(value: str) -> dict[str, Any] | None:
    """Decode a JSON object, returning None for non-object/invalid JSON."""

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def truncate_value(value: Any, *, max_chars: int) -> str:
    """Serialize and truncate a value for bounded log volume."""

    serialized = json.dumps(value, default=str, sort_keys=True)
    if len(serialized) <= max_chars:
        return serialized
    return f"{serialized[:max_chars]}...<truncated {len(serialized) - max_chars} chars>"

