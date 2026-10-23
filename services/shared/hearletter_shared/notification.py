"""Email the listener a link to their finished episode."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

DEFAULT_URL_TTL_SECONDS = 604800


@dataclass(frozen=True, slots=True)
class EpisodeEmail:
    to_email: str
    from_email: str
    title: str
    audio_url: str
    sources: tuple[str, ...]
    url_ttl_seconds: int | None  # None: permanent link (served through CloudFront)
    feed_url: str | None = None


def presigned_audio_url(s3_client: Any, *, bucket: str, key: str, expires_in: int) -> str:
    """Private, temporary link to the MP3.

    The link also stops working when the signing credentials expire, which for a Lambda role
    can be sooner than expires_in.
    """

    return str(
        s3_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_in
        )
    )


def send_episode_email(ses_client: Any, email: EpisodeEmail) -> str | None:
    """Send the episode-ready email and return the SES message id."""

    response = ses_client.send_email(
        Source=email.from_email,
        Destination={"ToAddresses": [email.to_email]},
        Message={
            "Subject": {"Data": subject_line(email.title), "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body(email), "Charset": "UTF-8"},
                "Html": {"Data": html_body(email), "Charset": "UTF-8"},
            },
        },
    )
    message_id = response.get("MessageId")
    return str(message_id) if message_id else None


def subject_line(title: str) -> str:
    return f"Your Hearletter briefing is ready: {title}"


def text_body(email: EpisodeEmail) -> str:
    lines = [
        email.title,
        "",
        f"Built from: {', '.join(email.sources)}",
        "",
        "Listen here:",
        email.audio_url,
    ]
    if email.url_ttl_seconds is not None:
        lines += ["", f"This link expires in {human_ttl(email.url_ttl_seconds)}."]
    if email.feed_url:
        lines += ["", "Or subscribe in your podcast app (keep this link private):", email.feed_url]
    return "\n".join(lines)


def html_body(email: EpisodeEmail) -> str:
    sources = escape(", ".join(email.sources))
    return (
        "<!doctype html><html><body>"
        f"<h1>{escape(email.title)}</h1>"
        f"<p>Built from: {sources}</p>"
        f'<p><a href="{escape(email.audio_url, quote=True)}">Listen to your briefing (MP3)</a></p>'
        + (
            f"<p>This link expires in {escape(human_ttl(email.url_ttl_seconds))}.</p>"
            if email.url_ttl_seconds is not None
            else ""
        )
        + (
            f'<p><a href="{escape(email.feed_url, quote=True)}">Subscribe in your podcast app</a>'
            " (keep this link private)</p>"
            if email.feed_url
            else ""
        )
        + "</body></html>"
    )


def human_ttl(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{seconds} seconds"
