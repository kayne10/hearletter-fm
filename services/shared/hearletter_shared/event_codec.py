"""Serialization helpers for SQS event payloads."""

from __future__ import annotations

import json
from dataclasses import is_dataclass
from enum import Enum
from typing import Any


class EventEncodingError(ValueError):
    """Raised when an event cannot be encoded or decoded."""


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return value.to_dict() if hasattr(value, "to_dict") else value.__dict__
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps_event(event: object) -> str:
    """Encode an event contract as compact JSON."""

    try:
        return json.dumps(event, default=_json_default, separators=(",", ":"), sort_keys=True)
    except TypeError as exc:
        raise EventEncodingError(str(exc)) from exc


def loads_event(body: str) -> dict[str, Any]:
    """Decode an SQS message body into a dictionary."""

    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise EventEncodingError(str(exc)) from exc
    if not isinstance(value, dict):
        raise EventEncodingError("Event body must decode to a JSON object")
    return value

