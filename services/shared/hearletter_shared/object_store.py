"""Minimal blob storage abstraction so handlers run against S3 or a local folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

CONDITIONAL_WRITE_LOST = {"PreconditionFailed", "ConditionalRequestConflict"}


class ObjectStore(Protocol):
    """The handful of storage operations the pipeline needs."""

    name: str

    def get_bytes(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        if_absent: bool = False,
        cache_control: str | None = None,
    ) -> bool:
        """Write an object. With if_absent, return False instead of overwriting."""
        ...


def get_json(store: ObjectStore, key: str) -> Any:
    return json.loads(store.get_bytes(key).decode("utf-8"))


def put_json(store: ObjectStore, key: str, value: Any, *, if_absent: bool = False) -> bool:
    body = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return store.put_bytes(
        key, body, content_type="application/json; charset=utf-8", if_absent=if_absent
    )


class S3ObjectStore:
    """ObjectStore backed by one S3 bucket."""

    def __init__(self, *, bucket: str, s3_client: Any) -> None:
        self.name = bucket
        self._s3 = s3_client

    def get_bytes(self, key: str) -> bytes:
        return bytes(self._s3.get_object(Bucket=self.name, Key=key)["Body"].read())

    def exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self.name, Key=key)
        except Exception as exc:  # botocore ClientError; avoid importing botocore at module load
            if _error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def list_keys(self, prefix: str) -> list[str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        return [
            str(item["Key"])
            for page in paginator.paginate(Bucket=self.name, Prefix=prefix)
            for item in page.get("Contents", [])
        ]

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        if_absent: bool = False,
        cache_control: str | None = None,
    ) -> bool:
        extra: dict[str, str] = {"IfNoneMatch": "*"} if if_absent else {}
        if cache_control:
            extra["CacheControl"] = cache_control
        try:
            self._s3.put_object(
                Bucket=self.name, Key=key, Body=body, ContentType=content_type, **extra
            )
        except Exception as exc:
            # 412: object already exists. 409: a concurrent conditional write won the race.
            if if_absent and _error_code(exc) in CONDITIONAL_WRITE_LOST:
                return False
            raise
        return True


class LocalObjectStore:
    """ObjectStore backed by a local directory, for local runs and tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.name = str(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        if not self.root.exists():
            return []
        keys = (path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        return sorted(key for key in keys if key.startswith(prefix) and self.exists(key))

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        if_absent: bool = False,
        cache_control: str | None = None,
    ) -> bool:
        path = self._path(key)
        if if_absent and path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return True


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code", ""))
    return ""
