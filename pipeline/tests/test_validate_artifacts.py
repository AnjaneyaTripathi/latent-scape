import importlib.util
import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest


def _validator():
    path = Path(__file__).parents[2] / "scripts" / "validate-artifacts.py"
    spec = importlib.util.spec_from_file_location("validate_artifacts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_original_outbound_pdf_is_valid_but_hosted_pdf_is_rejected(tmp_path):
    validator = _validator()
    assert validator.valid_outbound_paper_url("https://www.cs.cmu.edu/~yanliu/papers/icml03.pdf")
    assert not validator.valid_outbound_paper_url("/papers/icml03.pdf")
    assert not validator.valid_outbound_paper_url("ftp://example.org/paper.pdf")
    hosted = tmp_path / "paper.pdf"
    hosted.write_bytes(b"%PDF-not-a-real-paper")
    errors: list[str] = []
    validator.validate_hosted_files(tmp_path, [hosted], errors)
    assert any("PDF found in static artifact" in message for message in errors)


def test_optional_outbound_urls_allow_verified_pdfs_but_reject_unsafe_values():
    validator = _validator()
    assert validator.valid_optional_outbound_url(None)
    assert validator.valid_optional_outbound_url("")
    assert validator.valid_optional_outbound_url("https://publisher.example/paper.pdf#page=7")
    for unsafe in ("javascript:alert(1)", "data:text/html,unsafe", "/project", "relative/page", "https://[invalid", 7):
        assert not validator.valid_optional_outbound_url(unsafe)


def test_validator_rejects_operational_files_and_source_maps_from_reader_bundle(tmp_path):
    validator = _validator()
    coverage = tmp_path / "data" / "coverage.json"
    audit = tmp_path / "models" / "ASSET-AUDIT.json"
    source_map = tmp_path / "assets" / "index.js.map"
    for path in (coverage, audit, source_map):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    errors: list[str] = []
    validator.validate_hosted_files(tmp_path, [coverage, audit, source_map], errors)
    assert any("internal review or operational artifact" in message for message in errors)
    assert any("source map found" in message for message in errors)


def test_static_path_allowlist_rejects_unreferenced_working_and_review_files(tmp_path):
    validator = _validator()
    data = tmp_path / "data"
    data.mkdir()
    (data / "vectors.json").write_text('{"dimensions": 1, "shards": []}')
    working = tmp_path / "working" / "progress.json"
    review = tmp_path / "review.html"
    log = tmp_path / "logs" / "authoring.log"
    for path in (working, review, log):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private")
    errors: list[str] = []
    validator.validate_public_paths(tmp_path, [working, review, log], {"shards": {"papers": {}}}, errors)
    assert len([message for message in errors if "outside reader allowlist" in message]) == 3


@pytest.mark.parametrize("extension", ["woff2", "woff", "ttf"])
def test_local_math_fonts_are_allowed_and_remote_fonts_rejected(tmp_path, extension):
    validator = _validator()
    data = tmp_path / "data"
    data.mkdir()
    (data / "vectors.json").write_text('{"dimensions": 1, "shards": []}')
    (data / "topics.json").write_text('[]')
    font = tmp_path / "assets" / f"KaTeX_Main-Regular-hash.{extension}"
    font.parent.mkdir()
    font.write_bytes(b"local font fixture")
    errors: list[str] = []
    validator.validate_public_paths(tmp_path, [font], {"shards": {"papers": {}}}, errors)
    assert not errors
    stylesheet = font.parent / "index.css"
    stylesheet.write_text(f'@font-face{{src:url("https://cdn.example/font.{extension}")}}')
    validator.validate_hosted_files(tmp_path, [stylesheet], errors)
    assert any("external generated asset URL" in message for message in errors)


def test_favicon_is_a_parseable_allowlisted_runtime_asset(tmp_path):
    validator = _validator()
    root = Path(__file__).parents[2]
    favicon = root / "site" / "public" / "favicon.svg"
    ElementTree.parse(favicon)
    assert '%BASE_URL%favicon.svg' in (root / "site" / "index.html").read_text()
    assert "'./favicon.svg'" in (root / "site" / "public" / "sw.js").read_text()
    data = tmp_path / "data"
    data.mkdir()
    (data / "vectors.json").write_text('{"dimensions": 1, "shards": []}')
    (data / "topics.json").write_text('[]')
    copied = tmp_path / "favicon.svg"
    copied.write_text(favicon.read_text())
    errors: list[str] = []
    validator.validate_public_paths(tmp_path, [copied], {"shards": {"papers": {}}}, errors)
    assert not errors


def test_topic_graph_must_match_sanitized_topics_and_known_edges(tmp_path):
    validator = _validator()
    topics = [
        {"slug": "alpha", "title": "Alpha", "area": "Area", "broadGroupId": "broad-a", "paperSlugs": ["p1"], "relatedTopics": ["beta"]},
        {"slug": "beta", "title": "Beta", "area": "Area", "broadGroupId": "broad-a", "paperSlugs": ["p2"], "relatedTopics": []},
    ]
    graph = {
        "version": 3, "layoutKind": "focused-semantic-neighborhoods", "defaultTopicSlug": "alpha",
        "cardDimensions": {"root": {"width": 10, "height": 10}, "neighbor": {"width": 10, "height": 10}},
        "topics": [
            {"slug": "alpha", "title": "Alpha", "area": "Area", "paperCount": 1, "overview": "", "relatedTopics": [1]},
            {"slug": "beta", "title": "Beta", "area": "Area", "paperCount": 1, "overview": "", "relatedTopics": []},
        ],
        "neighborhoods": [
            {"rootTopic": 0, "bounds": {"width": 700, "height": 900}, "directNeighborTotal": 1,
             "nodes": [{"topic": 0, "degree": 0, "x": 0, "y": 0}, {"topic": 1, "degree": 1, "x": 10, "y": 10}],
             "edges": [{"source": 0, "target": 1, "degree": 1}]},
            {"rootTopic": 1, "bounds": {"width": 700, "height": 900}, "directNeighborTotal": 1,
             "nodes": [{"topic": 1, "degree": 0, "x": 0, "y": 0}, {"topic": 0, "degree": 1, "x": 10, "y": 10}],
             "edges": [{"source": 0, "target": 1, "degree": 1}]},
        ],
    }
    destination = tmp_path / "data" / "topic-graph.json"
    destination.parent.mkdir()
    destination.write_text(__import__("json").dumps(graph))
    errors: list[str] = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert not errors
    graph["neighborhoods"][0]["edges"] = []
    destination.write_text(__import__("json").dumps(graph))
    errors = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert any("edges are stale" in message for message in errors)


def test_topic_graph_rejects_out_of_bounds_card_geometry(tmp_path):
    validator = _validator()
    topics = [{"slug": "alpha", "title": "Alpha", "area": "Area", "paperSlugs": ["p1"], "relatedTopics": []}]
    graph = {
        "version": 3, "layoutKind": "focused-semantic-neighborhoods", "defaultTopicSlug": "alpha",
        "cardDimensions": {"root": {"width": 10, "height": 10}, "neighbor": {"width": 5, "height": 5}},
        "topics": [{"slug": "alpha", "title": "Alpha", "area": "Area", "paperCount": 1, "overview": "", "relatedTopics": []}],
        "neighborhoods": [{"rootTopic": 0, "bounds": {"width": 700, "height": 900}, "directNeighborTotal": 0, "nodes": [{"topic": 0, "degree": 0, "x": 695, "y": 0}], "edges": []}],
    }
    destination = tmp_path / "data" / "topic-graph.json"
    destination.parent.mkdir()
    destination.write_text(__import__("json").dumps(graph))
    errors: list[str] = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert any("falls outside bounds" in message for message in errors)


def test_topic_graph_requires_every_direct_neighbor_and_excludes_second_degree(tmp_path):
    """Keep all outgoing/incoming direct links, including those beyond the old cap."""
    validator = _validator()
    slugs = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
        "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
    ]
    topics = [
        {"slug": slug, "title": slug.title(), "area": "Area", "paperSlugs": [slug],
         "relatedTopics": (
             ["beta", "gamma", "delta", "epsilon", "zeta"] if slug == "alpha"
             else (["lambda", "mu", "nu", "xi", "omicron", "pi", "rho"] if slug == "beta"
                   else (["alpha"] if slug in {"eta", "theta"} else []))
         )}
        for slug in slugs
    ]
    rows = [
        {"slug": topic["slug"], "title": topic["title"], "area": topic["area"], "paperCount": 1,
         "overview": "", "relatedTopics": [slugs.index(related) for related in topic["relatedTopics"]]}
        for topic in topics
    ]
    direct = list(range(1, 8))
    root_neighborhood = {
        "rootTopic": 0, "bounds": {"width": 700, "height": 1200},
        "directNeighborTotal": 7,
        "nodes": [{"topic": 0, "degree": 0, "x": 0, "y": 0}, *[
            {"topic": topic, "degree": 1, "x": 20, "y": 20 + index * 130}
            for index, topic in enumerate(direct)
        ]],
        "edges": [
            *[{"source": 0, "target": topic, "degree": 1} for topic in direct],
        ],
    }
    graph = {
        "version": 3, "layoutKind": "focused-semantic-neighborhoods", "defaultTopicSlug": "alpha",
        "cardDimensions": {"root": {"width": 10, "height": 10}, "neighbor": {"width": 10, "height": 10}},
        "topics": rows,
        "neighborhoods": [root_neighborhood, *[
            {"rootTopic": index, "bounds": {"width": 700, "height": 900}, "directNeighborTotal": 0,
             "nodes": [{"topic": index, "degree": 0, "x": 0, "y": 0}], "edges": []}
            for index in range(1, len(slugs))
        ]],
    }
    destination = tmp_path / "data" / "topic-graph.json"
    destination.parent.mkdir()
    destination.write_text(__import__("json").dumps(graph))
    errors: list[str] = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert not any(message.endswith(": 0") for message in errors)
    graph["neighborhoods"][0]["nodes"].append({"topic": 8, "degree": 2, "x": 50, "y": 20})
    destination.write_text(__import__("json").dumps(graph))
    errors = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert any("neighborhood nodes are stale or inconsistent: 0" in message for message in errors)
    graph["neighborhoods"][0]["nodes"].pop()
    graph["neighborhoods"][0]["nodes"].pop()
    graph["neighborhoods"][0]["edges"].pop()
    graph["neighborhoods"][0]["directNeighborTotal"] = 6
    destination.write_text(__import__("json").dumps(graph))
    errors = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert any("neighborhood nodes are stale or inconsistent: 0" in message for message in errors)


def test_topic_paper_lists_require_full_ordered_compact_projection(tmp_path):
    validator = _validator()
    topics = [{
        "slug": "alpha", "title": "Alpha", "area": "Area", "overview": "Overview", "whyItMatters": "Why",
        "evolution": "Evolution", "openQuestions": ["Open"], "difficulty": "beginner", "paperSlugs": ["p2", "p1"],
        "relatedTopics": [], "prerequisites": [],
    }]
    papers = [
        {"slug": "p1", "title": "Paper 1", "authors": ["One"], "year": 2024, "venue": "ICML", "url": "https://example.test/p1", "originalUrl": None, "sourceLinkLabel": "Original paper", "summary": {"tldr": "One."}},
        {"slug": "p2", "title": "Paper 2", "authors": ["Two"], "year": 2023, "venue": "NeurIPS", "url": "https://example.test/p2", "originalUrl": "https://publisher.test/p2", "sourceLinkLabel": "Original paper", "summary": None},
    ]
    list_path = tmp_path / "data" / "topic-papers" / "alpha.json"
    list_path.parent.mkdir(parents=True)
    list_path.write_text(__import__("json").dumps({
        "version": 1,
        "topic": {"slug": "alpha", "title": "Alpha", "area": "Area", "overview": "Overview", "whyItMatters": "Why", "evolution": "Evolution", "openQuestions": ["Open"], "difficulty": "beginner", "paperCount": 2},
        "relatedTopics": [], "prerequisites": [],
        "papers": [
            {"slug": "p2", "title": "Paper 2", "authors": ["Two"], "year": 2023, "venue": "NeurIPS", "sourceUrl": "https://publisher.test/p2", "sourceLinkLabel": "Original paper"},
            {"slug": "p1", "title": "Paper 1", "authors": ["One"], "year": 2024, "venue": "ICML", "sourceUrl": "https://example.test/p1", "sourceLinkLabel": "Original paper", "tldr": "One."},
        ],
    }))
    errors: list[str] = []
    validator.validate_topic_paper_lists(tmp_path, topics, papers, errors)
    assert not errors
    value = __import__("json").loads(list_path.read_text())
    value["papers"] = value["papers"][:1]
    list_path.write_text(__import__("json").dumps(value))
    errors = []
    validator.validate_topic_paper_lists(tmp_path, topics, papers, errors)
    assert any("membership is incomplete" in message for message in errors)


@pytest.mark.parametrize("indexed_tldr", [None, "A complete", "An older summary."])
def test_search_summaries_reject_missing_truncated_or_stale_tldr(indexed_tldr):
    validator = _validator()
    tldr = "A complete published summary. " * 30
    papers = [{"slug": "p1", "summary": {"tldr": tldr}}]
    document = {"slug": "p1", "type": "paper"}
    if indexed_tldr is not None:
        document["tldr"] = indexed_tldr
    errors = []
    validator.validate_search_summaries([document], papers, errors)
    assert errors == ["search paper p1 TLDR is missing, truncated, or stale"]
    document["tldr"] = tldr
    errors = []
    validator.validate_search_summaries([document], papers, errors)
    assert not errors


@pytest.mark.parametrize("summary", [None, {}, {"tldr": ""}, {"tldr": 42}])
def test_search_summaries_require_absence_without_published_tldr(summary):
    validator = _validator()
    papers = [{"slug": "p1", "summary": summary}]
    document = {"slug": "p1", "type": "paper", "tldr": None}
    errors = []
    validator.validate_search_summaries([document], papers, errors)
    assert errors == ["search paper p1 has a TLDR without a published summary"]
    del document["tldr"]
    errors = []
    validator.validate_search_summaries([document], papers, errors)
    assert not errors


def test_search_summary_refresh_preserves_full_text_order_and_other_fields(tmp_path):
    script = Path(__file__).parents[2] / "scripts" / "build-search-summaries.mjs"
    tldr = "Published Unicode α summary with a complete final sentence. " * 20
    papers = [
        {"slug": "p1", "summary": {"tldr": tldr, "paragraphs": ["Full explanation."]}},
        {"slug": "p2", "summary": None},
    ]
    documents = [
        {"id": "p2", "slug": "p2", "type": "paper", "abstract": "Bounded excerpt", "tldr": "Removed summary."},
        {"id": "topic", "slug": "topic", "type": "topic", "description": "Unchanged."},
        {"id": "p1", "slug": "p1", "type": "paper", "abstract": "x" * 480, "tldr": tldr[:480]},
    ]
    (tmp_path / "papers").mkdir()
    shard_path = tmp_path / "papers" / "00.json"
    shard_path.write_text(json.dumps(papers), encoding="utf-8")
    shard_before = shard_path.read_bytes()
    (tmp_path / "manifest.json").write_text(json.dumps({"shards": {"papers": {"00": "papers/00.json"}}}))
    search_path = tmp_path / "search.json"
    search_path.write_text(json.dumps(documents, indent=2), encoding="utf-8")
    first = subprocess.run(["node", str(script), str(tmp_path)], check=True, capture_output=True, text=True)
    assert json.loads(first.stdout)["updated"] == 1
    assert json.loads(first.stdout)["removed"] == 1
    refreshed = json.loads(search_path.read_text(encoding="utf-8"))
    assert refreshed[2]["tldr"] == tldr
    assert "tldr" not in refreshed[0]
    without_tldr = lambda rows: [{key: value for key, value in row.items() if key != "tldr"} for row in rows]
    assert without_tldr(refreshed) == without_tldr(documents)
    assert shard_path.read_bytes() == shard_before
    first_bytes = search_path.read_bytes()
    second = subprocess.run(["node", str(script), str(tmp_path)], check=True, capture_output=True, text=True)
    assert json.loads(second.stdout)["updated"] == 0
    assert json.loads(second.stdout)["removed"] == 0
    assert search_path.read_bytes() == first_bytes
    errors = []
    _validator().validate_search_summaries(refreshed, papers, errors)
    assert not errors
