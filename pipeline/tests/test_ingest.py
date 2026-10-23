from pipeline.ingest import (
    _stratified_sample,
    discover_icml_volumes,
    extract_outbound_links,
    parse_neurips_index,
    parse_pmlr_volume,
)
from pipeline.util import stable_slug


def test_stable_slug_is_ascii_and_repeatable():
    assert stable_slug("Résumé: A/B", "NeurIPS", 2020) == stable_slug("Résumé: A/B", "NeurIPS", 2020)
    assert "resume" in stable_slug("Résumé", "NeurIPS", 2020)


def test_neurips_parses_track_links_without_pdf():
    html = '<ul><li><a href="/paper_files/paper/2025/hash/x-Abstract-Conference.html">A Paper</a> Ada, Bob</li><li><a href="x.pdf">PDF</a></li></ul>'
    papers = parse_neurips_index(html, 2025, "https://papers.nips.cc")
    assert len(papers) == 1
    assert papers[0]["title"] == "A Paper"


def test_pmlr_parses_missing_abstract_without_inventing_text():
    html = '<div class="paper"><p class="title">Paper Title</p><span class="authors"><a>Ada</a></span><a href="paper.html">abs</a></div>'
    papers = parse_pmlr_volume(html, 2024, "https://proceedings.mlr.press/v235/")
    assert papers[0]["abstract"] == ""
    assert papers[0]["authors"] == ["Ada"]


def test_discovers_only_labeled_icml_volumes():
    html = '<a href="/v306/">International Conference on Machine Learning 2026</a><a href="/v5/">Other 2026</a>'
    assert discover_icml_volumes(html) == {2026: "https://proceedings.mlr.press/v306/"}


def test_sample_covers_strata_before_extra_choices():
    papers = [{"venue": "ICML", "year": 2020, "slug": "a", "title": "One"}, {"venue": "NeurIPS", "year": 2021, "slug": "b", "title": "Two"}]
    assert {p["slug"] for p in _stratified_sample(papers, 2, 1)} == {"a", "b"}


def test_explicit_github_code_link_is_retained_without_punctuation():
    code, project = extract_outbound_links("Code available: https://github.com/CharlesQ9/HistAgent.")
    assert code == "https://github.com/CharlesQ9/HistAgent"
    assert project is None
