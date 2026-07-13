from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "email-parser"))

from handler import parse_ses_event, resolve_raw_email_ref  # noqa: E402


class FakeBody:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def read(self) -> bytes:
        return self._content


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects
        self.puts: list[dict[str, Any]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeBody]:
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}

    def put_object(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)


def test_resolve_raw_email_ref_from_lambda_action_event() -> None:
    event = ses_event()

    ref = resolve_raw_email_ref(
        event["Records"][0],
        env={"RAW_EMAIL_BUCKET": "raw-bucket", "RAW_EMAIL_PREFIX": "raw-email/default/"},
    )

    assert ref.bucket == "raw-bucket"
    assert ref.key == "raw-email/default/msg_123"


def test_parse_ses_event_writes_text_and_html_artifacts() -> None:
    raw_message = (
        b"From: newsletter@example.com\n"
        b"To: listen@example.com\n"
        b"Subject: Hello\n"
        b"Content-Type: multipart/alternative; boundary=frontier\n"
        b"\n"
        b"--frontier\n"
        b"Content-Type: text/plain; charset=utf-8\n"
        b"\n"
        b"Plain body\n"
        b"--frontier\n"
        b"Content-Type: text/html; charset=utf-8\n"
        b"\n"
        b"<p>HTML body</p>\n"
        b"--frontier--\n"
    )
    fake_s3 = FakeS3Client({("raw-bucket", "raw-email/default/msg_123"): raw_message})

    parsed = parse_ses_event(
        ses_event(),
        env={
            "RAW_EMAIL_BUCKET": "raw-bucket",
            "RAW_EMAIL_PREFIX": "raw-email/default/",
            "ARTIFACT_BUCKET": "artifact-bucket",
        },
        s3_client=fake_s3,
    )

    assert parsed.event_type == "newsletter.parsed"
    assert parsed.payload.raw_email.key == "raw-email/default/msg_123"
    assert parsed.payload.text is not None
    assert parsed.payload.html is not None
    assert len(fake_s3.puts) == 2


def ses_event() -> dict[str, Any]:
    return {
        "Records": [
            {
                "ses": {
                    "mail": {
                        "messageId": "msg_123",
                        "source": "newsletter@example.com",
                        "destination": ["listen@example.com"],
                        "commonHeaders": {"subject": "Hello"},
                    },
                    "receipt": {
                        "action": {
                            "type": "Lambda",
                            "functionArn": "arn:aws:lambda:us-east-2:123:function:parser",
                        }
                    },
                }
            }
        ]
    }

