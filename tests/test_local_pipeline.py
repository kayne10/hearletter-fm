from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from scripts import run_local_pipeline


def test_local_pipeline_runs_text_only_for_data_samples(tmp_path: Path) -> None:
    modules = run_local_pipeline.PipelineModules(
        email_parser=run_local_pipeline.load_module(
            "test_email_parser",
            run_local_pipeline.REPO_ROOT / "services" / "email-parser" / "handler.py",
        ),
        cleaner=run_local_pipeline.load_module(
            "test_newsletter_cleaner",
            run_local_pipeline.REPO_ROOT / "services" / "newsletter-cleaner" / "handler.py",
        ),
        summarizer=run_local_pipeline.load_module(
            "test_summarizer",
            run_local_pipeline.REPO_ROOT / "services" / "summarizer" / "handler.py",
        ),
    )

    outputs = [
        run_local_pipeline.run_sample(sample_path=sample, output_dir=tmp_path / sample.name, modules=modules)
        for sample in run_local_pipeline.discover_input_files(run_local_pipeline.REPO_ROOT / "data")
    ]

    assert {output["sample_kind"] for output in outputs} == {"raw_mime", "decoded_text"}
    assert all(output["clean_word_count"] > 500 for output in outputs)
    assert all(output["story_candidate_count"] >= 3 for output in outputs)
    assert not list(tmp_path.rglob("04_tts_request"))
    assert not list(tmp_path.rglob("04_polly_audio"))
    assert not list(tmp_path.rglob("script_draft.txt"))

    for output in outputs:
        context_path = Path(output["podcast_context"])
        if not context_path.is_absolute():
            context_path = run_local_pipeline.REPO_ROOT / context_path
        context = json.loads(context_path.read_text(encoding="utf-8"))
        ssml_path = context_path.parent / "polly_ssml.xml"
        ssml = ssml_path.read_text(encoding="utf-8")

        assert context["episode"]["mode"] == "morning_briefing"
        assert context["story_candidates"]
        assert "audio" not in context
        assert ssml.startswith("<speak>")
        assert "<break time=" in ssml
        assert ElementTree.fromstring(ssml).tag == "speak"
        assert output["audio_output"] is None
