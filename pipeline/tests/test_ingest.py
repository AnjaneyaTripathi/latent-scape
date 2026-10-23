from pipeline.ingest import (
    _stratified_sample,
    discover_icml_volumes,
    extract_outbound_links,
    parse_neurips_index,
    parse_pmlr_volume,
)
from pipeline.full_ingest import audit_full_abstract_quality, hydrate_official_page
from pipeline.settings import Settings
from pipeline.util import read_json, write_json
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


def test_full_hydration_preserves_official_abstract_as_display_text():
    page = """
    <h3>Abstract</h3><div>First sentence.\n Second sentence.</div>
    <meta name="citation_abstract" content="Different fallback" />
    <a href="https://github.com/example/work">Code repository</a>
    """
    hydrated = hydrate_official_page(page, {"slug": "paper", "abstract": "", "codeUrl": None, "projectUrl": None})
    assert hydrated["abstract"] == "First sentence. Second sentence."
    assert hydrated["originalAbstract"] == hydrated["abstract"]
    assert hydrated["abstractStatus"] == "present"
    assert hydrated["codeUrl"] == "https://github.com/example/work"


def test_quality_audit_removes_too_short_and_full_body_abstracts(tmp_path):
    settings = Settings(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        raw_dir=tmp_path / "raw",
        derived_dir=tmp_path / "derived",
        output_dir=tmp_path / "out",
    )
    settings.prepare()
    write_json(
        settings.raw_dir / "papers-full.json",
        [
            {"slug": "short", "abstract": "Abstract Unavailable", "originalAbstract": "Abstract Unavailable"},
            {
                "slug": "long",
                "abstract": ("word " * 698) + "Acknowledgements",
                "originalAbstract": ("word " * 698) + "Acknowledgements",
                "abstractQuality": "official-landing-page-possibly-full-text",
            },
            {"slug": "brief", "abstract": "word " * 10, "originalAbstract": "word " * 10},
            {"slug": "good", "abstract": "word " * 100, "originalAbstract": "word " * 100},
        ],
    )
    assert audit_full_abstract_quality(settings) == {
        "papers": 4,
        "rejected": 2,
        "review": 0,
        "summaryEvidenceInsufficient": 1,
        "placeholder-or-extraction-junk": 1,
        "legacy-landing-page-possibly-full-paper": 1,
    }
    papers = {paper["slug"]: paper for paper in read_json(settings.raw_dir / "papers-full.json", [])}
    assert papers["short"]["abstractStatus"] == "needs-source-recovery"
    assert papers["short"]["abstract"] == ""
    assert papers["long"]["originalAbstract"] == ""
    assert papers["brief"]["abstract"]
    assert papers["brief"]["summaryEvidenceStatus"] == "insufficient"
    assert papers["good"].get("abstractStatus") != "needs-source-recovery"
