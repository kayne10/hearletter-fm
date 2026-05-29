# Local Development

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Running a Handler Locally

Handlers are plain Python functions shaped like AWS Lambda handlers:

```python
def handler(event: dict[str, object], context: object) -> dict[str, object]:
    ...
```

For local development, create small JSON fixtures in `tests/fixtures/` and call the handler directly from tests. Keep AWS calls behind shared storage/client abstractions so unit tests can use in-memory fakes.

## Environment Variables

Common variables:

- `ARTIFACT_BUCKET`
- `AUDIO_BUCKET`
- `FEED_BUCKET`
- `NEXT_QUEUE_URL`
- `METADATA_TABLE`
- `OPENAI_API_KEY_SECRET_ARN`
- `TTS_PROVIDER`

## Testing Strategy

- Unit-test event contract serialization.
- Unit-test HTML cleaning with representative newsletter fixtures.
- Unit-test RSS XML output.
- Integration-test Lambda-to-SQS behavior with LocalStack later if useful.

Avoid making LocalStack mandatory for the first MVP. Most logic should be testable without AWS.

