import json
from types import SimpleNamespace

import pytest

from pipeline import astra_runner
from pipeline.astra_runner import MODEL, abstract_hash, batch_schema, load_exclusions, load_records, main, normalize_record, run_batch, valid_subset, validate_batch


def _paragraph(word: str) -> str:
    return " ".join([word] * 50)


def _summary(abstract: str, word: str = "detail") -> dict:
    return {
        "tldr": "A compact account of the paper.",
        "paragraphs": [_paragraph(word), _paragraph(word), _paragraph(word)],
        "difficulty": "intermediate", "source": "abstract", "model": MODEL,
        "backgroundConcepts": [], "sourceAbstractSha256": abstract_hash(abstract),
    }


def test_runner_validates_hash_and_narrative_word_budget():
    record = normalize_record({"slug": "paper", "title": "Paper", "abstract": "Official abstract.", "topicSlugs": ["topic"]})
    summary = {
        "tldr": "A compact account of the paper.",
        "paragraphs": [_paragraph("first"), _paragraph("second"), _paragraph("third")],
        "difficulty": "intermediate",
        "source": "abstract",
        "model": MODEL,
        "backgroundConcepts": ["topic"],
        "sourceAbstractSha256": abstract_hash("Official abstract."),
    }
    valid, errors = validate_batch([record], {"paper": summary})
    assert not errors
    assert valid == {"paper": summary}


def test_runner_dry_run_creates_resumable_manifest(tmp_path):
    source = tmp_path / "records.json"
    source.write_text(json.dumps([{"slug": "paper", "title": "Paper", "abstract": "official " * 40}]))
    output = tmp_path / "narratives"
    assert main(["--input", str(source), "--output-dir", str(output), "--dry-run"]) == 0
    manifest = json.loads((output / "working" / "manifest.json").read_text())
    assert manifest["eligibleRecords"] == 1
    assert manifest["batches"][0]["status"] == "planned"
    assert not list(output.glob("*.schema.json"))


def test_runner_rejects_different_input_for_resumable_directory(tmp_path):
    output = tmp_path / "narratives"
    one = tmp_path / "one.json"
    two = tmp_path / "two.json"
    one.write_text(json.dumps([{"slug": "one", "title": "One", "abstract": "official " * 40}]))
    two.write_text(json.dumps([{"slug": "two", "title": "Two", "abstract": "other " * 40}]))
    assert main(["--input", str(one), "--output-dir", str(output), "--dry-run"]) == 0
    with pytest.raises(SystemExit, match="different input"):
        main(["--input", str(two), "--output-dir", str(output), "--dry-run"])
    with pytest.raises(SystemExit, match="batch size"):
        main(["--input", str(one), "--output-dir", str(output), "--batch-size", "50", "--dry-run"])


def test_batch_schema_is_fixed_to_its_requested_slugs():
    record = normalize_record({"slug": "paper", "title": "Paper", "abstract": "Official abstract."})
    schema = batch_schema([record])
    assert schema["required"] == ["paper"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["paper"]["additionalProperties"] is False


def test_partial_checkpoint_keeps_only_valid_completed_records():
    record = normalize_record({"slug": "paper", "title": "Paper", "abstract": "Official abstract."})
    summary = {
        "tldr": "A compact account of the paper.",
        "paragraphs": [_paragraph("first"), _paragraph("second"), _paragraph("third")],
        "difficulty": "intermediate", "source": "abstract", "model": MODEL,
        "backgroundConcepts": [], "sourceAbstractSha256": abstract_hash(record.abstract),
    }
    valid, errors = valid_subset([record], {"paper": summary, "stale": summary})
    assert valid == {"paper": summary}
    assert errors["stale"] == ["partial checkpoint slug is not in this batch"]


def test_exclusion_snapshot_accepts_records_not_stringified_dicts(tmp_path):
    exclusions = tmp_path / "reviewed.json"
    exclusions.write_text(json.dumps([{"paperSlug": "reviewed-a"}, {"slug": "reviewed-b"}, "reviewed-c"]))
    assert load_exclusions(exclusions) == {"reviewed-a", "reviewed-b", "reviewed-c"}


def test_runner_marks_short_placeholder_and_untrusted_abstracts_unavailable(tmp_path):
    source = tmp_path / "records.json"
    source.write_text(json.dumps([
        {"slug": "short", "title": "Short", "abstract": "in"},
        {"slug": "missing", "title": "Missing", "abstract": "Abstract unavailable"},
        {"slug": "landing", "title": "Landing", "abstract": "word " * 41, "abstractQuality": "official-landing-page-possibly-full-text"},
        {"slug": "eligible", "title": "Eligible", "abstract": "word " * 40},
    ]))
    records, unavailable = load_records(source, None, None)
    assert [record.slug for record in records] == ["eligible"]
    assert {item["slug"] for item in unavailable} == {"short", "missing", "landing"}


def test_existing_dir_skips_only_matching_hash_valid_summaries(tmp_path):
    source = tmp_path / "records.json"
    abstract = "word " * 40
    source.write_text(json.dumps([{"slug": "paper", "title": "Paper", "abstract": abstract}]))
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    summary = _summary(abstract, "one")
    (shard_dir / "valid.json").write_text(json.dumps({"paper": summary}))
    records, unavailable = load_records(source, None, None, shard_dir)
    assert records == []
    assert unavailable[-1]["reason"] == "already summarized with matching abstract hash"
    summary["sourceAbstractSha256"] = abstract_hash("changed")
    (shard_dir / "valid.json").write_text(json.dumps({"paper": summary}))
    records, _ = load_records(source, None, None, shard_dir)
    assert [record.slug for record in records] == ["paper"]


def test_existing_dir_reuses_matching_summary_with_retired_background_label(tmp_path):
    source = tmp_path / "records.json"
    abstract = "word " * 40
    source.write_text(json.dumps([
        {"slug": "paper", "title": "Paper", "abstract": abstract, "topicSlugs": ["topic-current"]},
    ]))
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    summary = _summary(abstract, "one")
    summary["backgroundConcepts"] = ["human-label-from-preview"]
    (shard_dir / "reviewed.json").write_text(json.dumps({"paper": summary}))
    records, unavailable = load_records(source, None, None, shard_dir)
    assert records == []
    assert unavailable[-1]["reason"] == "already summarized with matching abstract hash"


def test_runner_resume_preserves_frozen_batches_without_another_model_call(tmp_path, monkeypatch):
    source = tmp_path / "records.json"
    abstract = "evidence " * 40
    source.write_text(json.dumps([{"slug": "paper", "title": "Paper", "abstract": abstract}]))
    output = tmp_path / "run"
    external = tmp_path / "reviewed"
    external.mkdir()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        destination = command[command.index("-o") + 1]
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump({"paper": _summary(abstract)}, handle)
        return SimpleNamespace(returncode=0, stdout='{"usage":{"input_tokens":5,"output_tokens":7}}\n', stderr="")

    monkeypatch.setattr(astra_runner.subprocess, "run", fake_run)
    arguments = ["--input", str(source), "--output-dir", str(output), "--skip-existing-dir", str(external), "--workers", "1"]
    assert main(arguments) == 0
    assert len(calls) == 1
    assert main(arguments) == 0
    assert len(calls) == 1
    manifest = json.loads((output / "working" / "manifest.json").read_text())
    assert manifest["batches"][0]["batch"] == 0
    assert manifest["reportedUsage"] == [{"batch": 0, "attempt": 1, "usage": {"input_tokens": 5, "output_tokens": 7}}]


def test_runner_keeps_all_retry_usage(tmp_path, monkeypatch):
    one = normalize_record({"slug": "one", "title": "One", "abstract": "one " * 40})
    two = normalize_record({"slug": "two", "title": "Two", "abstract": "two " * 40})
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        destination = command[command.index("-o") + 1]
        payload = {"one": _summary(one.abstract, "one")} if calls == 1 else {"two": _summary(two.abstract, "two")}
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return SimpleNamespace(returncode=0, stdout=f'{{"usage":{{"input_tokens":{calls},"output_tokens":{calls}}}}}\n', stderr="")

    monkeypatch.setattr(astra_runner.subprocess, "run", fake_run)
    working = tmp_path / "working"
    working.mkdir()
    result = run_batch(tmp_path, 0, [one, two], tmp_path, working, False)
    assert result["status"] == "complete"
    assert [detail["usage"]["input_tokens"] for detail in result["attemptsDetail"]] == [1, 2]
    assert calls == 2


def test_failed_batch_gets_a_new_bounded_retry_epoch_after_restart(tmp_path, monkeypatch):
    record = normalize_record({"slug": "paper", "title": "Paper", "abstract": "evidence " * 40})
    working = tmp_path / "working"
    working.mkdir()
    calls = 0

    def fail_then_succeed(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= 3:
            return SimpleNamespace(returncode=1, stdout='{"usage":{"input_tokens":1}}\n', stderr="offline")
        destination = command[command.index("-o") + 1]
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump({"paper": _summary(record.abstract)}, handle)
        return SimpleNamespace(returncode=0, stdout='{"usage":{"input_tokens":2}}\n', stderr="")

    monkeypatch.setattr(astra_runner.subprocess, "run", fail_then_succeed)
    first = run_batch(tmp_path, 0, [record], tmp_path, working, False)
    assert first["status"] == "failed"
    second = run_batch(tmp_path, 0, [record], tmp_path, working, False)
    assert second["status"] == "complete"
    assert calls == 4
    assert [entry["attempt"] for entry in second["attemptsDetail"]] == [1, 2, 3, 4]
