from pipeline.corpus import fingerprint, write_meta
import json
from pathlib import Path
from types import SimpleNamespace

from pipeline import astra_runner, embedding, full_build, topics
from pipeline.full_build import full_status, generate_full
from pipeline.summaries import abstract_sha256
from pipeline.settings import Settings
from pipeline.util import write_json


def test_full_status_marks_missing_narratives_pending(tmp_path):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    papers = [{"slug": "paper", "title": "Paper", "abstract": "Evidence " * 40, "topicSlugs": []}]
    write_json(settings.raw_dir / "papers-full.json", papers)
    write_json(settings.derived_dir / "papers-topics-full.json", papers)
    write_meta(settings.derived_dir / "papers-topics-full.json", "full", fingerprint(papers))
    status = full_status(settings)
    assert status["pendingSummaries"] == 1
    assert status["readyForRelease"] is False


def test_prepare_reuses_valid_reviewed_full_topology_without_reclustering(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    papers = [
        {"slug": "first", "title": "First", "abstract": "Evidence " * 40, "topicSlugs": ["controlled-topic-id"]},
        {"slug": "second", "title": "Second", "abstract": "Evidence " * 40, "topicSlugs": ["controlled-topic-id"]},
    ]
    write_json(settings.raw_dir / "papers-full.json", papers)
    value = fingerprint(papers)
    papers_path = settings.derived_dir / "papers-topics-full.json"
    write_json(papers_path, papers)
    write_meta(papers_path, "full", value)
    topics_path = settings.derived_dir / "topics-full.json"
    write_json(
        topics_path,
        [{
            "slug": "controlled-topic-id", "title": "Curated identity", "paperSlugs": ["first", "second"],
            "overview": "A reviewed guide.", "curationStatus": "complete", "broadGroupId": "broad-07",
        }],
    )
    write_meta(topics_path, "full", value)
    monkeypatch.setattr(embedding, "embed_corpus", lambda *args, **kwargs: {"reused": True})

    def must_not_cluster(*args, **kwargs):
        raise AssertionError("valid reviewed topology must not be reclustered")

    monkeypatch.setattr(topics, "cluster", must_not_cluster)
    result = full_build.prepare_full(settings)
    assert result["topologyReused"] is True
    assert result["clustering"] == {"topics": 1, "broadGroups": 1, "reused": True}
    assert json.loads(topics_path.read_text())[0]["slug"] == "controlled-topic-id"


def test_full_status_counts_narrative_summaries(tmp_path):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    papers = [{"slug": "paper", "title": "Paper", "abstract": "Evidence " * 40, "topicSlugs": []}]
    papers[0]["summary"] = {
        "tldr": "A valid Astra narrative.",
        "paragraphs": ["detail " * 80] * 3,
        "difficulty": "intermediate",
        "backgroundConcepts": [],
        "model": "gpt-6-astra",
        "source": "abstract",
        "sourceAbstractSha256": abstract_sha256(papers[0]),
    }
    write_json(settings.raw_dir / "papers-full.json", papers)
    write_json(settings.derived_dir / "papers-topics-full.json", papers)
    write_meta(settings.derived_dir / "papers-topics-full.json", "full", fingerprint(papers))
    write_json(
        settings.derived_dir / "topics-full.json",
        [{"slug": "topic", "curationStatus": "complete"}],
    )
    assert full_status(settings)["readyForRelease"] is True


def test_full_status_does_not_count_narrative_from_quarantined_abstract(tmp_path):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    papers = [{
        "slug": "paper", "title": "Paper", "abstract": "Evidence " * 40,
        "abstractQuality": "official-landing-page-possibly-full-text", "topicSlugs": [],
    }]
    papers[0]["summary"] = {
        "paragraphs": ["one", "two", "three"], "model": "gpt-6-astra",
        "source": "abstract", "sourceAbstractSha256": abstract_sha256(papers[0]),
    }
    raw = [{key: value for key, value in papers[0].items() if key != "summary"}]
    write_json(settings.raw_dir / "papers-full.json", raw)
    write_json(settings.derived_dir / "papers-topics-full.json", papers)
    write_meta(settings.derived_dir / "papers-topics-full.json", "full", fingerprint(raw))
    status = full_status(settings)
    assert status["narrativeSummaries"] == 0
    assert status["unavailablePapers"] == 1
    assert status["pendingEligibleSummaries"] == 0


def test_full_status_requires_a_schema_valid_narrative(tmp_path):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    paper = {"slug": "paper", "title": "Paper", "abstract": "Evidence " * 40, "topicSlugs": []}
    invalid = {
        "paragraphs": ["too short", "too short", "too short"],
        "model": "gpt-6-astra", "source": "abstract", "sourceAbstractSha256": abstract_sha256(paper),
    }
    write_json(settings.raw_dir / "papers-full.json", [paper])
    write_json(settings.derived_dir / "papers-topics-full.json", [{**paper, "summary": invalid}])
    write_meta(settings.derived_dir / "papers-topics-full.json", "full", fingerprint([paper]))
    status = full_status(settings)
    assert status["narrativeSummaries"] == 0
    assert status["pendingEligibleSummaries"] == 1


def test_generate_uses_frozen_exclusions_and_resumes_then_default_imports(tmp_path, monkeypatch):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    manual = {"slug": "manual", "title": "Manual", "abstract": "manual " * 40, "topicSlugs": ["topic-current"]}
    generated = {"slug": "generated", "title": "Generated", "abstract": "generated " * 40, "topicSlugs": []}
    papers = [manual, generated]
    write_json(settings.raw_dir / "papers-full.json", papers)
    write_json(settings.derived_dir / "papers-topics-full.json", papers)
    write_meta(settings.derived_dir / "papers-topics-full.json", "full", fingerprint(papers))
    write_json(settings.derived_dir / "topics-full.json", [])
    narrative_root = tmp_path / "data/generated/narratives"
    manual_summary = {
        "tldr": "Manual summary.", "paragraphs": ["manual " * 80] * 3,
        "difficulty": "intermediate", "source": "abstract", "model": "gpt-6-astra",
        "backgroundConcepts": ["retired-preview-label"], "sourceAbstractSha256": abstract_sha256(manual),
    }
    write_json(narrative_root / "manual.json", {"manual": manual_summary})
    # A prelude/review shard can land after an earlier dry-run snapshot. Before
    # a full-run manifest exists, generate must refresh it rather than reuse it.
    write_json(
        settings.derived_dir / "full-summary-exclusions.json",
        {"inputHash": "old-dry-run", "slugs": []},
    )
    model_calls = 0

    def fake_run(command, **kwargs):
        nonlocal model_calls
        model_calls += 1
        destination = command[command.index("-o") + 1]
        summary = {
            "tldr": "Generated summary.", "paragraphs": ["generated " * 80] * 3,
            "difficulty": "intermediate", "source": "abstract", "model": "gpt-6-astra",
            "backgroundConcepts": [], "sourceAbstractSha256": abstract_sha256(generated),
        }
        write_json(Path(destination), {"generated": summary})
        return SimpleNamespace(returncode=0, stdout='{"usage":{"input_tokens":2,"output_tokens":3}}\n', stderr="")

    class FakePopen:
        def __init__(self, command, cwd, stdout, stderr):
            self.pid = 12345
            self.code = astra_runner.main(command[3:])

        def wait(self):
            return self.code

    monkeypatch.setattr(astra_runner.subprocess, "run", fake_run)
    monkeypatch.setattr(full_build.subprocess, "Popen", FakePopen)
    first = generate_full(settings, workers=1)
    second = generate_full(settings, workers=1)
    assert first["generation"]["exitCode"] == 0
    assert second["generation"]["exitCode"] == 0
    assert model_calls == 1
    exclusions = json.loads((settings.derived_dir / "full-summary-exclusions.json").read_text())
    assert exclusions["slugs"] == ["manual"]
    from pipeline.summaries import import_summaries

    imported = import_summaries(settings, None, "full")
    assert imported["errors"] == {}
    assert imported["acceptedNarratives"] == 2


def test_run_cli_chains_generation_and_finalization_with_incomplete_flag(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text(
        "[pipeline]\ncache_dir = 'cache'\nraw_dir = 'raw'\nderived_dir = 'derived'\noutput_dir = 'output'\n"
        "[embedding]\nmodel = 'Xenova/all-MiniLM-L6-v2'\n"
    )
    received = {}

    def fake_run(settings, *, workers, allow_incomplete):
        received.update({"root": settings.root, "workers": workers, "allow": allow_incomplete})
        return {"ok": True}

    monkeypatch.setattr(full_build, "run_full", fake_run)
    full_build.main(["run", "--config", str(config), "--workers", "3", "--allow-incomplete"])
    assert received == {"root": Path.cwd(), "workers": 3, "allow": True}
    assert '"ok": true' in capsys.readouterr().out
