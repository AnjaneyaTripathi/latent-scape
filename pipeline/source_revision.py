"""Controlled source corrections that preserve reviewed topic IDs/topology.

This intentionally does not call KMeans. A correction changes source text and
vectors, not the already reviewed 256-topic membership or topic identifiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import numpy as np

from .corpus import fingerprint, write_meta
from .embedding import embed_corpus, source_hash
from .settings import Settings
from .topics import _representatives
from .util import read_json, write_json


REPAIRS_NAME = "abstract-collision-repairs.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision_id(repairs: dict[str, Any]) -> str:
    raw = str(repairs["inputRawSha256"])
    encoded = repr(repairs["updates"]).encode("utf-8")
    return f"collision-{raw[:12]}-{hashlib.sha256(encoded).hexdigest()[:12]}"


def _http_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def validate_repairs(settings: Settings, raw_path: Path | None = None) -> dict[str, Any]:
    """Validate the exact curator patch against the frozen raw source."""
    path = settings.raw_dir / REPAIRS_NAME
    repairs = read_json(path, {})
    if not isinstance(repairs, dict) or not isinstance(repairs.get("updates"), list):
        raise ValueError("repair input must be an object with updates")
    raw_path = raw_path or settings.raw_dir / "papers-full.json"
    if repairs.get("inputRawPath") != "pipeline/data/raw/papers-full.json":
        raise ValueError("repair input is not bound to papers-full.json")
    if repairs.get("inputRawSha256") != _sha256(raw_path):
        raise ValueError("repair input does not match the frozen raw corpus hash")
    raw = read_json(raw_path, [])
    by_slug = {paper.get("slug"): paper for paper in raw if isinstance(paper, dict)}
    updates = repairs["updates"]
    if len(updates) != 21 or len({item.get("slug") for item in updates if isinstance(item, dict)}) != 21:
        raise ValueError("repair input must contain exactly 21 uniquely slugged updates")
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("repair update must be an object")
        paper = by_slug.get(update.get("slug"))
        if not paper:
            raise ValueError(f"repair paper is absent: {update.get('slug')}")
        for field in ("title", "venue", "year"):
            if update.get(field) != paper.get(field):
                raise ValueError(f"repair identity mismatch for {update.get('slug')}: {field}")
        if update.get("expectedOldAbstractSha256") != hashlib.sha256(
            str(paper.get("abstract") or "").encode("utf-8")
        ).hexdigest():
            raise ValueError(f"repair old abstract hash mismatch: {update.get('slug')}")
        if not isinstance(update.get("abstract"), str) or update["abstract"] != update.get("originalAbstract"):
            raise ValueError(f"repair must supply identical abstract/originalAbstract: {update.get('slug')}")
        if update.get("abstract") == paper.get("abstract"):
            raise ValueError(f"repair does not change source text: {update.get('slug')}")
        if update.get("abstractStatus") != "present" or not _http_url(update.get("source")):
            raise ValueError(f"repair lacks verified source evidence: {update.get('slug')}")
        provenance = update.get("provenance")
        if not isinstance(provenance, list) or not all(_http_url(item.split("#", 1)[0]) for item in provenance):
            raise ValueError(f"repair provenance is invalid: {update.get('slug')}")
        if not isinstance(update.get("pdfStartPage"), int) or update["pdfStartPage"] < 1:
            raise ValueError(f"repair page evidence is invalid: {update.get('slug')}")
    return {"repairs": repairs, "raw": raw, "revision": _revision_id(repairs)}


def _archive(revision_dir: Path, paths: list[Path]) -> None:
    archive = revision_dir / "before"
    archive.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if path.exists():
            shutil.copy2(path, archive / path.name)


def _topic_membership(topics: list[dict]) -> dict[str, str]:
    membership: dict[str, str] = {}
    for topic in topics:
        for slug in topic.get("paperSlugs", []):
            if slug in membership:
                raise ValueError(f"paper appears in multiple topic memberships: {slug}")
            membership[slug] = topic["slug"]
    return membership


def _rewrite_curation_input(
    settings: Settings, value: str, papers: list[dict], vectors: np.ndarray, topics: list[dict]
) -> None:
    by_slug = {paper["slug"]: index for index, paper in enumerate(papers)}
    fine: list[dict] = []
    broad: dict[str, list[str]] = defaultdict(list)
    for topic in topics:
        indices = np.asarray([by_slug[slug] for slug in topic["paperSlugs"]], dtype=np.int64)
        center = vectors[indices].mean(axis=0)
        center /= max(float(np.linalg.norm(center)), 1e-12)
        years = [papers[index]["year"] for index in indices]
        broad[topic["broadGroupId"]].append(topic["slug"])
        fine.append({"topicSlug": topic["slug"], "broadGroupId": topic["broadGroupId"], "count": len(indices), "yearSpan": {"start": min(years), "end": max(years)}, "representatives": _representatives(papers, indices, vectors, center)})
    write_json(settings.derived_dir / "topics-curation-input-full.json", {"corpus": "full", "corpusFingerprint": value, "fineTopics": fine, "broadGroups": [{"broadGroupId": key, "childTopicSlugs": sorted(slugs)} for key, slugs in sorted(broad.items())]})


def apply_source_revision(settings: Settings) -> dict[str, Any]:
    """Apply 21 corrections; only those papers may move to old centroids."""
    repair_input = read_json(settings.raw_dir / REPAIRS_NAME, {})
    if not isinstance(repair_input, dict) or not isinstance(repair_input.get("updates"), list):
        raise ValueError("repair input must be an object with updates")
    revision = _revision_id(repair_input)
    revision_dir = settings.derived_dir / "source-revisions" / revision
    report_path = revision_dir / "report.json"
    if report_path.exists():
        return read_json(report_path, {})
    raw_path = settings.raw_dir / "papers-full.json"
    archived_raw = revision_dir / "before" / raw_path.name
    if raw_path.exists() and repair_input.get("inputRawSha256") != _sha256(raw_path) and archived_raw.exists():
        shutil.copy2(archived_raw, raw_path)
    checked = validate_repairs(settings)
    repairs, raw = checked["repairs"], checked["raw"]
    papers_path = settings.derived_dir / "papers-topics-full.json"
    topics_path = settings.derived_dir / "topics-full.json"
    old_papers = read_json(papers_path, [])
    topics = read_json(topics_path, [])
    if not isinstance(old_papers, list) or not isinstance(topics, list) or len(old_papers) != len(raw):
        raise ValueError("current papers/topics snapshot is unavailable for a controlled revision")
    old_membership = _topic_membership(topics)
    if set(old_membership) != {paper["slug"] for paper in old_papers}:
        raise ValueError("topic membership does not cover the current papers snapshot")
    vectors_path = settings.derived_dir / "embeddings-full.npy"
    old_vectors = np.load(vectors_path)
    if old_vectors.shape[0] != len(raw):
        raise ValueError("old embeddings are not aligned to the frozen source order")
    _archive(revision_dir, [settings.raw_dir / "papers-full.json", papers_path, topics_path, vectors_path, settings.derived_dir / "topics-curation-input-full.json", settings.derived_dir / "full-paper-summary-input.json", settings.derived_dir / "full-summary-exclusions.json"])
    write_json(revision_dir / "state.json", {"revision": revision, "stage": "archived", "oldRawSha256": repairs["inputRawSha256"], "oldCorpusFingerprint": fingerprint(raw)})
    updates = {item["slug"]: item for item in repairs["updates"]}
    for paper in raw:
        update = updates.get(paper["slug"])
        if update:
            for key in ("abstract", "originalAbstract", "abstractStatus", "abstractSource", "abstractQuality", "abstractRecoveryLocked", "provenance", "source", "pdfStartPage", "sourceIssue", "collisionGroupId", "abstractExtraction"):
                paper[key] = update[key]
            paper["verifiedOriginalUrl"] = f"{update['source']}#page={update['pdfStartPage']}"
    write_json(settings.raw_dir / "papers-full.json", raw)
    write_json(revision_dir / "state.json", {"revision": revision, "stage": "raw-applied", "oldRawSha256": repairs["inputRawSha256"], "oldCorpusFingerprint": fingerprint(read_json(archived_raw, []))})
    # Keep an auditable, commit-eligible copy separate from ignored raw inputs.
    write_json(settings.root / "data/generated" / f"source-correction-{revision}.json", {key: repairs[key] for key in ("inputRawSha256", "sourcePolicy", "validation", "updates")})
    embedded = embed_corpus(settings, "full")
    vectors = np.load(settings.derived_dir / "embeddings-full.npy")
    if len(vectors) != len(raw) or not np.all(np.isfinite(vectors)):
        raise ValueError("corrected embeddings are not aligned to the revised source order")
    old_by_slug = {paper["slug"]: paper for paper in old_papers}
    # Assign only repaired papers to the nearest *old* centroid. Centres exclude
    # the repaired papers so a newly corrected vector cannot pull its own former
    # cluster toward an erroneous source. Every other membership is untouched.
    old_by_slug_index = {paper["slug"]: index for index, paper in enumerate(old_papers)}
    topic_order = [topic["slug"] for topic in topics]
    old_centers: list[np.ndarray] = []
    for topic in topics:
        indices = [old_by_slug_index[slug] for slug in topic["paperSlugs"] if slug not in updates]
        if not indices:
            indices = [old_by_slug_index[slug] for slug in topic["paperSlugs"]]
        center = old_vectors[np.asarray(indices)].mean(axis=0)
        old_centers.append(center / max(float(np.linalg.norm(center)), 1e-12))
    old_centers_array = np.asarray(old_centers, dtype=np.float32)
    raw_index = {paper["slug"]: index for index, paper in enumerate(raw)}
    reassignments: dict[str, dict[str, str]] = {}
    new_membership = dict(old_membership)
    for slug in sorted(updates):
        old_topic = old_membership[slug]
        nearest = int(np.argmax(old_centers_array @ vectors[raw_index[slug]]))
        new_topic = topic_order[nearest]
        new_membership[slug] = new_topic
        reassignments[slug] = {"oldTopicSlug": old_topic, "newTopicSlug": new_topic}
    # Keep every existing topic ID meaningful. A pathological repair set could
    # otherwise empty a small source topic; retain its closest corrected member
    # instead of deleting an established topic ID.
    for topic_slug in topic_order:
        if any(member == topic_slug for member in new_membership.values()):
            continue
        candidates = [slug for slug in updates if old_membership[slug] == topic_slug]
        if not candidates:
            raise ValueError(f"source revision would empty topic without a repair candidate: {topic_slug}")
        keep = max(candidates, key=lambda slug: float(old_centers_array[topic_order.index(topic_slug)] @ vectors[raw_index[slug]]))
        new_membership[keep] = topic_slug
        reassignments[keep]["newTopicSlug"] = topic_slug
    for topic in topics:
        topic["paperSlugs"] = [slug for slug in topic["paperSlugs"] if new_membership[slug] == topic["slug"]]
    for slug in sorted(updates):
        destination = new_membership[slug]
        if slug not in next(topic for topic in topics if topic["slug"] == destination)["paperSlugs"]:
            next(topic for topic in topics if topic["slug"] == destination)["paperSlugs"].append(slug)
    for topic in topics:
        topic["paperSlugs"].sort(key=lambda slug: (raw[raw_index[slug]]["year"], slug))
    revised_papers: list[dict] = []
    for paper in raw:
        prior = dict(old_by_slug[paper["slug"]])
        if paper["slug"] in updates:
            prior.update({key: paper.get(key) for key in ("abstract", "originalAbstract", "abstractStatus", "abstractSource", "abstractQuality", "abstractRecoveryLocked", "source", "pdfStartPage", "sourceIssue", "collisionGroupId", "abstractExtraction", "verifiedOriginalUrl")})
            prior["provenance"] = sorted(set((prior.get("provenance") or []) + (paper.get("provenance") or [])))
            prior.pop("summary", None)
            prior["topicSlugs"] = [
                new_membership[paper["slug"]] if slug == old_membership[paper["slug"]] else slug
                for slug in prior.get("topicSlugs", [])
            ]
        revised_papers.append(prior)
    value = fingerprint(raw)
    write_json(papers_path, revised_papers)
    write_meta(papers_path, "full", value)
    actual_membership = _topic_membership(topics)
    if actual_membership != new_membership:
        raise ValueError("revised topic membership is inconsistent")
    for slug, old_topic in old_membership.items():
        if slug not in updates and actual_membership[slug] != old_topic:
            raise ValueError("an unchanged paper moved topic during source revision")
    by_slug = {paper["slug"]: index for index, paper in enumerate(revised_papers)}
    centers = []
    for topic in topics:
        indices = np.asarray([by_slug[slug] for slug in topic["paperSlugs"]], dtype=np.int64)
        center = vectors[indices].mean(axis=0)
        centers.append(center / max(float(np.linalg.norm(center)), 1e-12))
    centers_array = np.asarray(centers, dtype=np.float32)
    similarity = centers_array @ centers_array.T
    for index, topic in enumerate(topics):
        neighbours = np.argsort(similarity[index])[::-1][1:6]
        topic["relatedTopics"] = [topics[int(other)]["slug"] for other in neighbours]
    write_json(topics_path, topics)
    write_meta(topics_path, "full", value)
    _rewrite_curation_input(settings, value, revised_papers, vectors, topics)
    summary_rows = [{"paperSlug": paper["slug"], "title": paper["title"], "abstract": paper.get("abstract", ""), "topicSlugs": paper.get("topicSlugs", []), "abstractQuality": paper.get("abstractQuality", "")} for paper in revised_papers]
    write_json(settings.derived_dir / "full-paper-summary-input.json", summary_rows)
    exclusions = read_json(settings.derived_dir / "full-summary-exclusions.json", {})
    if isinstance(exclusions, dict) and isinstance(exclusions.get("slugs"), list):
        exclusions["slugs"] = [slug for slug in exclusions["slugs"] if slug not in updates]
        exclusions["inputHash"] = hashlib.sha256((settings.derived_dir / "full-paper-summary-input.json").read_bytes()).hexdigest()
        write_json(settings.derived_dir / "full-summary-exclusions.json", exclusions)
    old_evidence = {row.get("topicSlug"): row for row in read_json(revision_dir / "before" / "topics-curation-input-full.json", {}).get("fineTopics", [])} if (revision_dir / "before" / "topics-curation-input-full.json").exists() else {}
    affected = sorted({topic for change in reassignments.values() for topic in change.values()})
    current_evidence = {row["topicSlug"]: row for row in read_json(settings.derived_dir / "topics-curation-input-full.json", {}).get("fineTopics", [])}
    write_json(settings.root / "data/generated" / f"affected-topic-guide-review-{revision}.json", {"revision": revision, "newCorpusFingerprint": value, "affectedTopicSlugs": affected, "reassignments": reassignments, "topics": [{"topicSlug": slug, "priorGuideProvenance": next(topic for topic in topics if topic["slug"] == slug).get("provenance"), "previousEvidence": old_evidence.get(slug), "currentEvidence": current_evidence.get(slug)} for slug in affected], "instruction": "Review these guides against current representative evidence and source/destination membership changes. Do not treat this artifact as an approval or restamp existing guide fingerprints."})
    report = {"revision": revision, "repairs": len(updates), "oldRawSha256": repairs["inputRawSha256"], "newRawSha256": _sha256(settings.raw_dir / "papers-full.json"), "oldCorpusFingerprint": fingerprint(read_json(archived_raw, [])), "newCorpusFingerprint": value, "affectedTopicSlugs": affected, "reassignments": reassignments, "preservedTopicIds": sorted(topic["slug"] for topic in topics), "unchangedMembershipsPreserved": True, "embeddings": embedded, "guideFingerprintsRestamped": False}
    write_json(report_path, report)
    write_json(revision_dir / "state.json", {"revision": revision, "stage": "complete", "report": str(report_path)})
    return report


def apply_verified_link_metadata(settings: Settings) -> dict[str, int]:
    """Attach page-precise original-source links after a completed revision.

    This changes only evidence metadata; title/abstract fingerprint inputs are
    intentionally unchanged.
    """
    repairs = read_json(settings.raw_dir / REPAIRS_NAME, {})
    updates = repairs.get("updates", []) if isinstance(repairs, dict) else []
    raw_path = settings.raw_dir / "papers-full.json"
    raw = read_json(raw_path, [])
    derived_path = settings.derived_dir / "papers-topics-full.json"
    derived = read_json(derived_path, [])
    raw_by_slug = {paper.get("slug"): paper for paper in raw}
    derived_by_slug = {paper.get("slug"): paper for paper in derived}
    applied = 0
    for update in updates:
        paper = raw_by_slug.get(update.get("slug"))
        if not paper or paper.get("abstract") != update.get("abstract"):
            raise ValueError(f"cannot attach page metadata to unmatched repair: {update.get('slug')}")
        values = {"source": update["source"], "pdfStartPage": update["pdfStartPage"], "verifiedOriginalUrl": f"{update['source']}#page={update['pdfStartPage']}"}
        paper.update(values)
        if update["slug"] in derived_by_slug:
            derived_by_slug[update["slug"]].update(values)
        applied += 1
    write_json(raw_path, raw)
    write_json(derived_path, derived)
    revision = _revision_id(repairs)
    report_path = settings.derived_dir / "source-revisions" / revision / "report.json"
    report = read_json(report_path, {})
    archived = settings.derived_dir / "source-revisions" / revision / "before" / "papers-full.json"
    if isinstance(report, dict) and archived.exists():
        report["newRawSha256"] = _sha256(raw_path)
        report["oldCorpusFingerprint"] = fingerprint(read_json(archived, []))
        write_json(report_path, report)
    return {"repairs": applied}


def restore_derived_provenance(settings: Settings) -> dict[str, int]:
    """Restore enrichment provenance from the immutable pre-revision snapshot."""
    repairs = read_json(settings.raw_dir / REPAIRS_NAME, {})
    revision = _revision_id(repairs)
    archive = settings.derived_dir / "source-revisions" / revision / "before" / "papers-topics-full.json"
    current_path = settings.derived_dir / "papers-topics-full.json"
    before, current = read_json(archive, []), read_json(current_path, [])
    if not isinstance(before, list) or not isinstance(current, list):
        raise ValueError("source revision provenance archive is unavailable")
    before_by_slug = {paper.get("slug"): paper for paper in before}
    restored = 0
    for paper in current:
        previous = before_by_slug.get(paper.get("slug"), {})
        merged = sorted(set((previous.get("provenance") or []) + (paper.get("provenance") or [])))
        if merged != paper.get("provenance", []):
            paper["provenance"] = merged
            restored += 1
    write_json(current_path, current)
    return {"restored": restored}


def _content_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def apply_approved_guide_carryforward(settings: Settings) -> dict[str, Any]:
    """Restamp guides only when the Astra revision approval binds every hash."""
    approval_path = settings.root / "data/generated/source-revision-topic-approval.json"
    approval = read_json(approval_path, {})
    raw_path = settings.raw_dir / "papers-full.json"
    current_fingerprint = fingerprint(read_json(raw_path, []))
    if not isinstance(approval, dict) or approval.get("status") != "approved":
        raise ValueError("source revision guide approval is missing or not approved")
    # Verified page-link metadata may be added after text correction; the guide
    # approval binds the title/abstract fingerprint, not incidental JSON layout.
    if approval.get("newCorpusFingerprint") != current_fingerprint:
        raise ValueError("guide approval is not bound to the current corrected source")
    revision = str(approval["revision"])
    result_path = settings.derived_dir / "source-revisions" / revision / "guide-carryforward.json"
    if result_path.exists():
        result = read_json(result_path, {})
        report_path = settings.derived_dir / "source-revisions" / revision / "report.json"
        report = read_json(report_path, {})
        if isinstance(report, dict):
            report["guideFingerprintsRestamped"] = True
            report["guideCarryForward"] = result
            write_json(report_path, report)
        return result
    approved = {row["topicSlug"]: row["guideContentSha256"] for row in [*approval.get("topics", []), *approval.get("unchangedOtherGuides", [])] if isinstance(row, dict) and row.get("carryForwardApproved")}
    guide_paths = sorted((settings.root / "data/generated").glob("topics-full-*.json"))
    guides: dict[str, dict] = {}
    for path in guide_paths:
        rows = read_json(path, {})
        for slug, guide in rows.items():
            if slug not in approved or _content_hash(guide) != approved[slug]:
                raise ValueError(f"guide approval hash mismatch: {slug}")
            guides[slug] = guide
    if len(guides) != 256 or set(guides) != set(approved):
        raise ValueError("guide approval does not cover exactly the current 256 guides")
    archive = settings.derived_dir / "source-revisions" / revision / "before-guides"
    archive.mkdir(parents=True, exist_ok=True)
    for path in [*guide_paths, settings.root / "data/generated/broad-areas-full.json"]:
        if path.exists() and not (archive / path.name).exists():
            shutil.copy2(path, archive / path.name)
    for path in guide_paths:
        rows = read_json(path, {})
        for guide in rows.values():
            guide["corpusFingerprint"] = current_fingerprint
        write_json(path, rows)
    broad_path = settings.root / "data/generated/broad-areas-full.json"
    broad = read_json(broad_path, {})
    if not approval.get("allBroadNamesCarryForwardApproved") or len(broad) != 32:
        raise ValueError("broad-area approval is incomplete")
    for guide in broad.values():
        guide["corpusFingerprint"] = current_fingerprint
    write_json(broad_path, broad)
    topics_path = settings.derived_dir / "topics-full.json"
    topics = read_json(topics_path, [])
    for topic in topics:
        provenance = dict(topic.get("provenance") or {})
        provenance["corpusFingerprint"] = current_fingerprint
        provenance["sourceRevision"] = revision
        provenance["guideCarryForwardApproval"] = str(approval_path.relative_to(settings.root))
        topic["provenance"] = provenance
        topic["curationStatus"] = "complete"
    write_json(topics_path, topics)
    write_meta(topics_path, "full", current_fingerprint)
    result = {"revision": revision, "guides": len(guides), "broadAreas": len(broad), "newCorpusFingerprint": current_fingerprint, "approvalSha256": _sha256(approval_path)}
    write_json(result_path, result)
    report_path = settings.derived_dir / "source-revisions" / revision / "report.json"
    report = read_json(report_path, {})
    if isinstance(report, dict):
        report["guideFingerprintsRestamped"] = True
        report["guideCarryForward"] = result
        write_json(report_path, report)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "apply", "metadata", "restore-provenance", "carry-guides"))
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    from .settings import load_settings
    settings = load_settings(Path.cwd(), args.config)
    if args.command == "validate":
        result = validate_repairs(settings)
        output = {"repairs": len(result["repairs"]["updates"]), "revision": result["revision"]}
    elif args.command == "metadata":
        output = apply_verified_link_metadata(settings)
    elif args.command == "carry-guides":
        output = apply_approved_guide_carryforward(settings)
    elif args.command == "restore-provenance":
        output = restore_derived_provenance(settings)
    else:
        output = apply_source_revision(settings)
    print(__import__("json").dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
