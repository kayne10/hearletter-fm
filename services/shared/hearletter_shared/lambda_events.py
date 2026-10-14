"""Helpers for structured Lambda logging."""

from __future__ import annotations

import json
import os
from typing import Any

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
    """Summarize SQS and S3 Lambda events without dumping large payloads."""

    records = event.get("Records")
    if not isinstance(records, list):
        return {"shape": "direct", "keys": sorted(event.keys())}
    return {
        "shape": "records",
        "record_count": len(records),
        "records": [summarize_record(item) for item in records[:10] if isinstance(item, dict)],
    }


def summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Summarize one SQS record (wrapping an S3 notification) or one S3 record."""

    if "body" in record:
        decoded = decode_json_object(str(record.get("body", ""))) or {}
        return {
            "source": "sqs",
            "message_id": record.get("messageId"),
            "s3_keys": [s3_key(inner) for inner in decoded.get("Records", []) if "s3" in inner],
        }
    if "s3" in record:
        return {"source": "s3", "event_name": record.get("eventName"), "s3_key": s3_key(record)}
    return {
        "source": record.get("eventSource") or record.get("EventSource") or "unknown",
        "keys": sorted(record.keys()),
    }


def s3_key(record: dict[str, Any]) -> str:
    s3 = record.get("s3", {})
    return f"{s3.get('bucket', {}).get('name')}/{s3.get('object', {}).get('key')}"


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


def log_json(message: str, **fields: Any) -> None:
    """Emit one structured CloudWatch log line."""

    print(json.dumps({"message": message, **fields}, default=str, sort_keys=True))
