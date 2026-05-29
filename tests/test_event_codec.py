from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import EventEnvelope, ParsedNewsletterPayload, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event, loads_event


def test_event_round_trip() -> None:
    now = utc_now_iso()
    event = EventEnvelope(
        event_id=new_id("evt"),
        event_type="newsletter.parsed",
        schema_version="1.0",
        correlation_id="msg_123",
        tenant_id="default",
        newsletter_id="nws_123",
        occurred_at=now,
        payload=ParsedNewsletterPayload(
            raw_email=S3ObjectRef(bucket="raw", key="raw-email/default/msg.eml"),
            sender="newsletter@example.com",
            recipient="listen@example.com",
            subject="Hello",
            received_at=now,
            html=None,
            text=None,
        ),
    )

    decoded = loads_event(dumps_event(event))

    assert decoded["event_type"] == "newsletter.parsed"
    assert decoded["payload"]["raw_email"]["key"] == "raw-email/default/msg.eml"

