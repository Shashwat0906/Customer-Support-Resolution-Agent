"""Ingest policy documents: parse metadata, split into sections, embed, store.

Usage: python -m scripts.ingest_policies

Idempotent: documents are upserted by ID and their chunks replaced, so the
command can be re-run after editing a policy.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete

from app.db.models import PolicyChunk, PolicyDocument
from app.db.session import session_scope, wait_for_db
from app.policies.embeddings import embed

log = logging.getLogger("ingest")
DOCS_DIR = Path(__file__).resolve().parent.parent / "app" / "policies" / "documents"
_SECTION = re.compile(r"^## +(\d+)\.\s*(.+)$", re.MULTILINE)


def parse_document(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path.name}: missing front matter")
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front)
    for key in ("id", "policy_key", "version", "title", "topic", "effective_from", "source_uri"):
        if not meta.get(key):
            raise ValueError(f"{path.name}: missing metadata field {key!r}")
    meta["content_hash"] = hashlib.sha256(raw.encode()).hexdigest()
    sections = []
    matches = list(_SECTION.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[m.end() : end].strip()
        if text:
            sections.append({"number": m.group(1), "title": m.group(2).strip(), "text": text})
    if not sections:
        raise ValueError(f"{path.name}: no sections found")
    return meta, sections


def _as_date(v: Any) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def ingest(docs_dir: Path = DOCS_DIR) -> int:
    count = 0
    with session_scope() as session:
        for path in sorted(docs_dir.glob("*.md")):
            meta, sections = parse_document(path)
            doc = session.get(PolicyDocument, meta["id"]) or PolicyDocument(id=meta["id"])
            doc.policy_key = meta["policy_key"]
            doc.version = str(meta["version"])
            doc.title = meta["title"]
            doc.topic = meta["topic"]
            doc.effective_from = _as_date(meta["effective_from"])  # type: ignore[assignment]
            doc.effective_to = _as_date(meta.get("effective_to"))
            doc.source_uri = meta["source_uri"]
            doc.content_hash = meta["content_hash"]
            session.add(doc)
            session.flush()
            session.execute(delete(PolicyChunk).where(PolicyChunk.document_id == doc.id))
            texts = [f"{doc.title}. {s['title']}. {s['text']}" for s in sections]
            vectors = embed(texts)
            for s, text, vec in zip(sections, texts, vectors, strict=True):
                session.add(
                    PolicyChunk(
                        id=f"{doc.id}#s{s['number']}",
                        document_id=doc.id,
                        content=s["text"],
                        embedding=vec,
                        metadata_={
                            "section_ref": f"§{s['number']}",
                            "section_title": s["title"],
                            "policy_key": doc.policy_key,
                            "version": doc.version,
                            "topic": doc.topic,
                            "title": doc.title,
                            "embedded_text": text,
                        },
                    )
                )
                count += 1
    return count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    wait_for_db()
    log.info("ingested %s policy chunks", ingest())


if __name__ == "__main__":
    main()
