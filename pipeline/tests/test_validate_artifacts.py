import importlib.util
from pathlib import Path


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


def test_topic_graph_must_match_sanitized_topics_and_known_edges(tmp_path):
    validator = _validator()
    topics = [
        {"slug": "alpha", "title": "Alpha", "area": "Area", "broadGroupId": "broad-a", "paperSlugs": ["p1"], "relatedTopics": ["beta"]},
        {"slug": "beta", "title": "Beta", "area": "Area", "broadGroupId": "broad-a", "paperSlugs": ["p2"], "relatedTopics": []},
    ]
    graph = {
        "version": 1, "layoutKind": "focused-semantic-neighborhoods", "defaultTopicSlug": "alpha",
        "bounds": {"width": 100, "height": 100}, "cardDimensions": {"root": {"width": 10, "height": 10}, "neighbor": {"width": 10, "height": 10}},
        "topics": [
            {"slug": "alpha", "title": "Alpha", "area": "Area", "paperCount": 1, "overview": "", "relatedTopics": [1]},
            {"slug": "beta", "title": "Beta", "area": "Area", "paperCount": 1, "overview": "", "relatedTopics": []},
        ],
        "neighborhoods": [
            {"rootTopic": 0, "directNeighborTotal": 1, "secondDegreeTotal": 0,
             "nodes": [{"topic": 0, "degree": 0, "x": 0, "y": 0}, {"topic": 1, "degree": 1, "x": 10, "y": 10}],
             "edges": [{"source": 0, "target": 1, "degree": 1}]},
            {"rootTopic": 1, "directNeighborTotal": 1, "secondDegreeTotal": 0,
             "nodes": [{"topic": 1, "degree": 0, "x": 0, "y": 0}], "edges": []},
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
        "version": 1, "layoutKind": "focused-semantic-neighborhoods", "defaultTopicSlug": "alpha",
        "bounds": {"width": 20, "height": 20}, "cardDimensions": {"root": {"width": 10, "height": 10}, "neighbor": {"width": 5, "height": 5}},
        "topics": [{"slug": "alpha", "title": "Alpha", "area": "Area", "paperCount": 1, "overview": "", "relatedTopics": []}],
        "neighborhoods": [{"rootTopic": 0, "directNeighborTotal": 0, "secondDegreeTotal": 0, "nodes": [{"topic": 0, "degree": 0, "x": 15, "y": 0}], "edges": []}],
    }
    destination = tmp_path / "data" / "topic-graph.json"
    destination.parent.mkdir()
    destination.write_text(__import__("json").dumps(graph))
    errors: list[str] = []
    validator.validate_topic_graph(tmp_path, topics, errors)
    assert any("falls outside bounds" in message for message in errors)
