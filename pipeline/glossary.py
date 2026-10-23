from __future__ import annotations

from .corpus import select, verify_meta, write_meta
from .settings import Settings
from .util import read_json, write_json


def glossary(settings: Settings, corpus: str = "sample") -> dict:
    _, value = select(settings, corpus)
    path = settings.derived_dir / f"topics-{corpus}.json"
    verify_meta(path, corpus, value, "topics")
    topics = read_json(path, [])
    items = [{"term": t["title"], "topicSlug": t["slug"], "definition": t["overview"]} for t in topics]
    output = settings.derived_dir / f"glossary-{corpus}.json"
    write_json(output, items)
    write_meta(output, corpus, value)
    return {"terms": len(items)}
