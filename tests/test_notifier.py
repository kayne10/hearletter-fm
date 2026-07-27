from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def load_notifier() -> Any:
    handler_path = Path(__file__).resolve().parents[1] / "services" / "notifier" / "handler.py"
    spec = importlib.util.spec_from_file_location("test_notifier_handler", handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load notifier handler from {handler_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeS3Client:
    def __init__(self) -> None:
        self.presign_calls: list[dict[str, Any]] = []

    def generate_presigned_url(self, client_method: str, **kwargs: Any) -> str:
        self.presign_calls.append({"client_method": client_method, **kwargs})
        params = kwargs["Params"]
        return f"https://signed.example/{params['Bucket']}/{params['Key']}"


class FakeSesClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send_email(self, **kwargs: Any) -> dict[str, str]:
        self.messages.append(kwargs)
        return {"MessageId": "ses-message-123"}


def test_notifier_emails_presigned_audio_link() -> None:
    notifier = load_notifier()
    fake_s3 = FakeS3Client()
    fake_ses = FakeSesClient()

    result = notifier.process_event(
        generated_episode_event(notification_email="listener@example.com"),
        s3_client=fake_s3,
        ses_client=fake_ses,
        from_email="no-reply@example.com",
        url_ttl_seconds=3600,
    )

    assert result["status"] == "sent"
    assert result["message_id"] == "ses-message-123"
    assert result["to_email"] == "listener@example.com"
    assert fake_s3.presign_calls[0]["client_method"] == "get_object"
    assert fake_s3.presign_calls[0]["ExpiresIn"] == 3600
    message = fake_ses.messages[0]
    assert message["Source"] == "no-reply@example.com"
    assert message["Destination"]["ToAddresses"] == ["listener@example.com"]
    text_body = message["Message"]["Body"]["Text"]["Data"]
    assert "https://signed.example/audio-bucket/audio/default/ep_123.mp3" in text_body


def test_notifier_skips_without_notification_email() -> None:
    notifier = load_notifier()
    fake_s3 = FakeS3Client()
    fake_ses = FakeSesClient()

    result = notifier.process_event(
        generated_episode_event(notification_email=None),
        s3_client=fake_s3,
        ses_client=fake_ses,
        from_email="no-reply@example.com",
    )

    assert result == {
        "status": "skipped",
        "reason": "missing_notification_email",
        "event_id": "evt_123",
        "newsletter_id": "nws_123",
    }
    assert fake_s3.presign_calls == []
    assert fake_ses.messages == []


def generated_episode_event(*, notification_email: str | None) -> dict[str, Any]:
    return {
        "event_id": "evt_123",
        "event_type": "episode.generated",
        "schema_version": "1.0",
        "correlation_id": "msg_123",
        "tenant_id": "default",
        "newsletter_id": "nws_123",
        "occurred_at": "2026-07-27T12:00:00Z",
        "payload": {
            "episode_id": "ep_123",
            "title": "Morning Briefing - 2026-07-27",
            "audio": {"bucket": "audio-bucket", "key": "audio/default/ep_123.mp3"},
            "audio_url": "https://example.invalid/audio/default/ep_123.mp3",
            "mime_type": "audio/mpeg",
            "byte_length": 123,
            "duration_seconds": None,
            "published_at": "2026-07-27T12:00:00Z",
            "notification_email": notification_email,
        },
    }
