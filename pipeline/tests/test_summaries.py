from jsonschema import Draft202012Validator

from pipeline.settings import Settings
from pipeline.summaries import SUMMARY_SCHEMA, abstract_sha256, import_summaries, narrative_prompt
from pipeline.util import read_json, write_json


def test_narrative_prompt_binds_exact_official_abstract():
    paper = {"slug": "paper", "title": "Paper", "abstract": "Official source text."}
    prompt = narrative_prompt(paper)
    assert prompt["abstract"] == "Official source text."
    assert prompt["sourceAbstractSha256"] == abstract_sha256(paper)
    assert prompt["sourceAbstractSha256"] in prompt["prompt"]


def test_narrative_schema_accepts_three_paragraph_explanation():
    summary = {
        "tldr": "A compact account of the work.",
        "paragraphs": ["A" * 80, "B" * 80, "C" * 80],
        "difficulty": "intermediate",
        "source": "abstract",
        "model": "gpt-6-astra",
        "backgroundConcepts": [],
        "sourceAbstractSha256": "a" * 64,
    }
    assert not list(Draft202012Validator(SUMMARY_SCHEMA).iter_errors(summary))


def _narrative(paper: dict, marker: str) -> dict:
    return {
        "tldr": f"{marker} TLDR.",
        "paragraphs": [f"{marker} " * 80, f"{marker} " * 80, f"{marker} " * 80],
        "difficulty": "intermediate", "source": "abstract", "model": "gpt-6-astra",
        "backgroundConcepts": [], "sourceAbstractSha256": abstract_sha256(paper),
    }


def _full_settings(tmp_path):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    paper = {"slug": "paper", "title": "Paper", "abstract": "Current official abstract. " * 40, "topicSlugs": []}
    write_json(settings.raw_dir / "papers-full.json", [paper])
    write_json(settings.derived_dir / "topics-full.json", [])
    return settings, paper


def test_directory_import_prefers_current_hash_narrative_over_stale_candidate(tmp_path):
    settings, paper = _full_settings(tmp_path)
    directory = tmp_path / "narratives"
    directory.mkdir()
    stale = _narrative({**paper, "abstract": "Older source text."}, "stale")
    fresh = _narrative(paper, "fresh")
    write_json(directory / "old.json", {"paper": stale})
    write_json(directory / "new.json", {"paper": fresh})
    write_json(directory / "archive" / "conflict.json", {"paper": _narrative(paper, "ignored")})
    result = import_summaries(settings, directory, "full")
    assert result["errors"] == {}
    assert result["acceptedNarratives"] == 1
    assert result["skippedStale"] == 1
    assert read_json(settings.derived_dir / "papers-topics-full.json")[0]["summary"]["tldr"] == "fresh TLDR."


def test_explicit_stale_file_is_rejected(tmp_path):
    settings, paper = _full_settings(tmp_path)
    stale = _narrative({**paper, "abstract": "Older source text."}, "stale")
    source = tmp_path / "stale.json"
    write_json(source, {"paper": stale})
    result = import_summaries(settings, source, "full")
    assert "paper" in result["errors"]


def test_ineligible_source_clears_previously_imported_narrative(tmp_path):
    settings = Settings(tmp_path, tmp_path / "cache", tmp_path / "raw", tmp_path / "derived", tmp_path / "output")
    paper = {
        "slug": "paper", "title": "Paper", "abstract": "Evidence " * 40,
        "abstractQuality": "official-landing-page-possibly-full-text", "topicSlugs": [],
    }
    prior = _narrative(paper, "prior")
    current = [{**paper, "summary": prior}]
    write_json(settings.raw_dir / "papers-full.json", [paper])
    write_json(settings.derived_dir / "papers-topics-full.json", current)
    from pipeline.corpus import fingerprint, write_meta

    write_meta(settings.derived_dir / "papers-topics-full.json", "full", fingerprint([paper]))
    write_json(settings.derived_dir / "topics-full.json", [])
    source = tmp_path / "narratives"
    write_json(source / "old.json", {"paper": prior})
    imported = import_summaries(settings, source, "full")
    assert imported["errors"] == {}
    assert imported["skippedIneligible"] == 1
    assert "summary" not in read_json(settings.derived_dir / "papers-topics-full.json")[0]
    strict = tmp_path / "strict.json"
    write_json(strict, {"paper": prior})
    rejected = import_summaries(settings, strict, "full")
    assert "paper" in rejected["errors"]
