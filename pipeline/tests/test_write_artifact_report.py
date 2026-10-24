import importlib.util
from pathlib import Path

from pipeline.summary_evidence import summary_eligibility_reason as export_eligibility_reason


def _reporter():
    path = Path(__file__).parents[2] / "scripts" / "write-artifact-report.py"
    spec = importlib.util.spec_from_file_location("write_artifact_report", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_report_uses_export_evidence_gate_for_summary_counts():
    reporter = _reporter()
    long_abstract = " ".join(["evidence"] * 40)
    papers = [
        {"venue": "ICML", "year": 2024, "summary": {"paragraphs": ["Explains the work."]}, "abstract": long_abstract},
        {"venue": "NeurIPS", "year": 2024, "summary": {"tldr": "Older summary."}, "abstract": long_abstract},
        {"venue": "ICML", "year": 2023, "summary": None, "abstract": long_abstract},
        {"venue": "ICML", "year": 2022, "summary": None, "abstract": "too short"},
        {"venue": "NeurIPS", "year": 2021, "summary": None, "abstract": long_abstract, "abstractQuality": "official-landing-page-possibly-full-text"},
    ]
    assert reporter.summary_coverage(papers) == {"narrative": 1, "legacy": 1, "pending": 1, "unavailable": 2}
    assert reporter.venue_year_counts(papers) == {"ICML:2022": 1, "ICML:2023": 1, "ICML:2024": 1, "NeurIPS:2021": 1, "NeurIPS:2024": 1}
    for abstract, quality in ((long_abstract, ""), ("too short", ""), (long_abstract, "official-landing-page-possibly-full-text")):
        assert reporter.summary_eligibility_reason(abstract, quality) == export_eligibility_reason(abstract, quality)
