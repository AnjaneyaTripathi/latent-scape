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
