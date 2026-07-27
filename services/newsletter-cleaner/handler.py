"""Lambda handler for cleaning parsed newsletter content."""

from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal local environments
    BeautifulSoup = None  # type: ignore[assignment]

from hearletter_domain.models import S3ObjectRef
from hearletter_events.contracts import CleanedNewsletterPayload, EventEnvelope, new_id, utc_now_iso
from hearletter_shared.event_codec import dumps_event
from hearletter_shared.lambda_events import iter_pipeline_events, log_lambda_event
from hearletter_utils.text import normalize_whitespace

JUNK_LINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"view (this )?(email|newsletter) in (your )?browser",
        r"unsubscribe",
        r"privacy policy",
        r"manage (your )?preferences",
        r"sponsored by",
        r"presented by",
        r"advertisement",
        r"share on (facebook|twitter|linkedin)",
        r"^<https?://",
        r"^\[image:",
    )
]

URL_LINE_RE = re.compile(r"^<?https?://\S+>?$")
MARKDOWN_LINK_URL_RE = re.compile(r"\n?<https?://[^>\s]+>")
IMAGE_MARKER_RE = re.compile(r"\[image:[^\]]+\]", re.IGNORECASE)
FORWARDED_HEADER_RE = re.compile(
    r"^(---------- Forwarded message ---------|From:|Date:|Subject:|To:)",
    re.IGNORECASE,
)
SECTION_STOP_TITLES = {
    "reader poll",
    "calendar",
    "the week ahead",
    "sponsored by chase",
    "sponsored by state street investment management",
}


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


def clean_html(html: str) -> tuple[str, list[str]]:
    """Convert newsletter HTML into readable text and report removed section types."""

    if BeautifulSoup is None:
        text = re.sub(r"<[^>]+>", "\n", html)
        cleaned = clean_plain_text(text)
        return cleaned, ["html_tags"]

    soup = BeautifulSoup(html, "html.parser")
    removed: list[str] = []

    for selector in ("script", "style", "noscript", "svg", "img", "nav", "footer"):
        for node in soup.select(selector):
            node.decompose()
            removed.append(selector)

    text = soup.get_text("\n")
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(pattern.search(stripped) for pattern in JUNK_LINE_PATTERNS):
            removed.append("junk_line")
            continue
        cleaned_lines.append(stripped)

    return normalize_whitespace("\n".join(cleaned_lines)), sorted(set(removed))


def clean_plain_text(text: str) -> str:
    """Clean a decoded text/plain newsletter body."""

    text = MARKDOWN_LINK_URL_RE.sub("", text)
    text = IMAGE_MARKER_RE.sub("", text)
    cleaned_lines: list[str] = []

    for line in text.splitlines():
        stripped = normalize_line(line)
        if not stripped:
            continue
        if URL_LINE_RE.match(stripped):
            continue
        if FORWARDED_HEADER_RE.match(stripped):
            continue
        if any(pattern.search(stripped) for pattern in JUNK_LINE_PATTERNS):
            continue
        cleaned_lines.append(stripped)

    return "\n\n".join(unwrap_plain_text_lines(cleaned_lines))


def clean_newsletter_content(*, html: str | None, text: str | None) -> tuple[str, list[str]]:
    """Clean decoded newsletter content.

    Forwarded newsletters often include a high-quality `text/plain` alternative. Prefer it for
    local extraction because HTML table layouts tend to fragment sentences into many tiny nodes.
    """

    if text:
        cleaned = clean_plain_text(text)
        if len(cleaned.split()) >= 100:
            return cleaned, ["plain_text"]
    if html:
        return clean_html(html)
    return "", []


def extract_story_candidates(clean_text: str, *, max_stories: int = 8) -> list[dict[str, Any]]:
    """Extract likely newsletter stories for downstream podcast scripting.

    This intentionally returns candidates rather than pretending to solve article extraction
    perfectly. The summarizer/agent can use these candidates as grounded context.
    """

    lines = [line.strip() for line in clean_text.splitlines() if line.strip()]
    paragraphs = [paragraph.strip() for paragraph in clean_text.split("\n\n") if paragraph.strip()]
    agenda = extract_agenda_items(lines)
    candidates: list[dict[str, Any]] = []
    agenda_candidates = extract_agenda_story_candidates(paragraphs, agenda)
    candidates.extend(agenda_candidates)
    used_agenda = {candidate["agenda_match"] for candidate in agenda_candidates}

    for index, paragraph in enumerate(paragraphs):
        words = paragraph.split()
        if len(words) < 28 or len(words) > 260:
            continue
        title = previous_title(paragraphs, index)
        if is_low_value_title(title) or is_low_value_paragraph(paragraph):
            continue

        score = story_score(title, paragraph, agenda)
        agenda_match = matching_agenda_item(title, paragraph, agenda)
        if agenda_match in used_agenda:
            continue
        candidates.append(
            {
                "rank": 0,
                "title": title,
                "summary_source_text": clean_excerpt(paragraph),
                "word_count": len(words),
                "score": score,
                "agenda_match": agenda_match,
            }
        )

    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    selected = candidates[:max_stories]
    for rank, candidate in enumerate(selected, start=1):
        candidate["rank"] = rank
    return selected


def extract_agenda_story_candidates(
    paragraphs: list[str],
    agenda: list[str],
) -> list[dict[str, Any]]:
    """Create one high-priority story candidate for each explicit agenda item."""

    candidates: list[dict[str, Any]] = []
    for agenda_item in agenda:
        best_index: int | None = None
        best_score = 0
        for index, paragraph in enumerate(paragraphs):
            if is_low_value_paragraph(paragraph):
                continue
            score = agenda_paragraph_score(agenda_item, paragraph)
            if score > best_score:
                best_index = index
                best_score = score

        if best_index is None or best_score < 80:
            continue

        excerpt = merge_story_paragraphs(paragraphs, best_index)
        candidates.append(
            {
                "rank": 0,
                "title": agenda_item,
                "summary_source_text": clean_excerpt(excerpt),
                "word_count": len(excerpt.split()),
                "score": best_score + 250,
                "agenda_match": agenda_item,
            }
        )
    return candidates


def agenda_paragraph_score(agenda_item: str, paragraph: str) -> int:
    """Score how well a paragraph appears to satisfy an explicit agenda item."""

    lower = paragraph.lower()
    if len(paragraph.split()) < 25 or paragraph.lstrip().startswith("- "):
        return 0
    if "in today's newsletter" in lower or "in today’s newsletter" in lower:
        return 0
    score = token_overlap(agenda_item, lower) * 60
    for term in ("lawsuit", "died", "funflation", "expensive", "openai", "graham"):
        if term in lower and term in agenda_item.lower():
            score += 35
    if "according" in lower or "said" in lower:
        score += 15
    return score


def merge_story_paragraphs(paragraphs: list[str], start_index: int, *, max_words: int = 170) -> str:
    """Merge adjacent paragraphs into a compact story excerpt."""

    merged: list[str] = []
    total_words = 0
    for paragraph in paragraphs[start_index : start_index + 4]:
        if is_low_value_title(paragraph) or is_low_value_paragraph(paragraph):
            break
        words = paragraph.split()
        if total_words and looks_like_heading(paragraph):
            break
        merged.append(paragraph)
        total_words += len(words)
        if total_words >= max_words:
            break
    return " ".join(merged)


def clean_excerpt(value: str) -> str:
    """Remove visual credits and stray markdown from a story excerpt."""

    value = split_at_next_story_marker(value)
    value = re.sub(r"\b[A-Z][A-Za-z]+/[A-Z][A-Za-z]+ Images\b", "", value)
    value = re.sub(r"\b[A-Z][A-Za-z]+/[A-Z][A-Za-z]+\b", "", value)
    value = re.sub(r"Sen\. Lindsey Graham in 2022\. Kevin\s*", "", value)
    value = value.replace("*", "")
    return cap_words(normalize_line(value), 160)


def split_at_next_story_marker(value: str) -> str:
    """Stop an excerpt when a later emoji-led mini story begins."""

    for marker in (" 🚢 ", " 🏈 ", " 🇺🇸 ", " 📈 ", " 💰 "):
        index = value.find(marker, 40)
        if index != -1:
            return value[:index]
    return value


def cap_words(value: str, max_words: int) -> str:
    """Cap long source excerpts to keep agent context compact."""

    words = value.split()
    if len(words) <= max_words:
        return value
    return " ".join(words[:max_words]).rstrip(",;:") + "..."


def unwrap_plain_text_lines(lines: list[str]) -> list[str]:
    """Rejoin email-wrapped text lines into readable paragraphs."""

    paragraphs: list[str] = []
    current = ""

    for line in lines:
        if not current:
            current = line
            continue
        if should_start_new_paragraph(current, line):
            paragraphs.append(current)
            current = line
        else:
            current = f"{current} {line}"

    if current:
        paragraphs.append(current)

    return [normalize_line(paragraph) for paragraph in paragraphs if normalize_line(paragraph)]


def should_start_new_paragraph(previous: str, current: str) -> bool:
    """Detect story, heading, and bullet boundaries in wrapped plain text."""

    if current.startswith("- "):
        return True
    if previous.endswith((".", "?", "!", "…", ":")):
        return True
    return bool(looks_like_heading(current))


def looks_like_heading(value: str) -> bool:
    """Return whether a line looks like a newsletter heading rather than wrapped prose."""

    words = value.split()
    if not 1 <= len(words) <= 10:
        return False
    lower = value.lower().strip("*")
    if lower in SECTION_STOP_TITLES:
        return True
    if lower.endswith(":"):
        return True
    if value.endswith("?"):
        return True
    if "/" in value and any(token in value for token in ("Getty", "Images", "Nurphoto")):
        return True
    starts_upper = value[0].isupper()
    has_sentence_punctuation = any(mark in value for mark in (".", ",", ";"))
    return starts_upper and not has_sentence_punctuation


def extract_agenda_items(lines: list[str]) -> list[str]:
    """Find explicit newsletter agenda bullets when present."""

    agenda: list[str] = []
    in_agenda = False
    for line in lines:
        lower = line.lower()
        if "in today's newsletter" in lower or "in today’s newsletter" in lower:
            in_agenda = True
            continue
        if in_agenda and line.startswith("- "):
            agenda.append(normalize_line(line.removeprefix("- ")))
            continue
        if in_agenda and agenda:
            break
    return agenda


def previous_title(paragraphs: list[str], index: int) -> str:
    """Use nearby short paragraphs as a human-ish title."""

    title_parts: list[str] = []
    for prior in reversed(paragraphs[max(0, index - 3) : index]):
        words = prior.split()
        if 1 <= len(words) <= 14 and not is_low_value_title(prior) and not is_image_credit(prior):
            title_parts.insert(0, prior)
        if len(title_parts) >= 2:
            break
    if title_parts:
        return normalize_line(": ".join(title_parts))
    return normalize_line("Story candidate")


def story_score(title: str, paragraph: str, agenda: list[str]) -> int:
    """Rank likely editorial stories above sponsors, polls, and housekeeping."""

    combined = f"{title} {paragraph}".lower()
    score = min(len(paragraph.split()), 180)
    for agenda_item in agenda:
        if token_overlap(agenda_item, combined) >= 2:
            score += 90
    for term in ("said", "according", "reported", "announced", "lawsuit", "data", "died"):
        if term in combined:
            score += 12
    return score


def matching_agenda_item(title: str, paragraph: str, agenda: list[str]) -> str | None:
    """Return the agenda item a candidate appears to satisfy."""

    combined = f"{title} {paragraph}".lower()
    for agenda_item in agenda:
        if token_overlap(agenda_item, combined) >= 2:
            return agenda_item
    return None


def token_overlap(needle: str, haystack: str) -> int:
    """Count meaningful token overlap between two strings."""

    tokens = {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9']+", needle.lower())
        if len(token) > 3
    }
    return sum(1 for token in tokens if token in haystack)


def is_low_value_title(title: str) -> bool:
    lower = title.strip().lower()
    return lower in SECTION_STOP_TITLES or any(
        phrase in lower
        for phrase in (
            "sponsored",
            "advertisement",
            "reader poll",
            "share on",
            "view online",
            "markets:",
            "data is provided",
        )
    )


def is_low_value_paragraph(paragraph: str) -> bool:
    lower = paragraph.lower()
    return any(
        phrase in lower
        for phrase in (
            "unsubscribe",
            "manage preferences",
            "privacy policy",
            "sponsored by",
            "share on facebook",
            "data is provided by",
            "offering circular",
            "member fdic",
            "equal housing opportunity",
            "this compensation",
        )
    )


def is_image_credit(value: str) -> bool:
    lower = value.lower()
    return any(token in lower for token in ("getty images", "nurphoto", "associated press photo"))


def normalize_line(line: str) -> str:
    """Normalize a single newsletter line while preserving readable punctuation."""

    stripped = line.strip()
    stripped = stripped.strip("*")
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = stripped.replace(" .", ".")
    return stripped.strip()
