"""Policy retrieval (RAG) with version filtering, citations and gap detection.

Hybrid scoring: cosine similarity over chunk embeddings + PostgreSQL full-text
rank. Candidates are first filtered to documents *in effect* on the relevant
date (the order's delivery date), so a superseded policy version is never cited.

Retrieved passages are policy *text* for citation and explanation. They are
marked untrusted and never decide eligibility; the deterministic rules in
``app/policies/rules.py`` do.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.db.models import PolicyChunk, PolicyDocument
from app.policies.embeddings import cosine, embed, tokens

TOPICS = ("refunds", "returns", "damage", "shipping", "exceptions")


class PolicyFilters(BaseModel):
    topics: list[str] | None = Field(default=None, description=f"Subset of {TOPICS}")
    effective_on: date | None = Field(default=None, description="Only documents in effect on this date")
    policy_keys: list[str] | None = None


class PolicyPassage(BaseModel):
    chunk_id: str
    document_id: str
    policy_key: str
    version: str
    title: str
    section_ref: str
    section_title: str
    content: str
    score: float
    source_uri: str
    untrusted: bool = True


class PolicySearchResult(BaseModel):
    query: str
    effective_on: date | None
    passages: list[PolicyPassage]
    applicable_documents: list[dict[str, str]]
    missing_topics: list[str]
    conflicts: list[dict[str, object]]


def _tsquery(query: str) -> str:
    toks = [re.sub(r"[^a-z0-9]", "", t) for t in tokens(query)]
    return " | ".join(t for t in toks if t) or "policy"


def search_policy(
    session: Session, query: str, filters: PolicyFilters | None = None, top_k: int = 5
) -> PolicySearchResult:
    filters = filters or PolicyFilters()
    stmt = select(PolicyDocument)
    if filters.topics:
        stmt = stmt.where(PolicyDocument.topic.in_(filters.topics))
    if filters.policy_keys:
        stmt = stmt.where(PolicyDocument.policy_key.in_(filters.policy_keys))
    if filters.effective_on:
        stmt = stmt.where(
            PolicyDocument.effective_from <= filters.effective_on,
            or_(PolicyDocument.effective_to.is_(None), PolicyDocument.effective_to >= filters.effective_on),
        )
    docs = session.scalars(stmt).all()

    by_key: dict[str, list[PolicyDocument]] = {}
    for d in docs:
        by_key.setdefault(d.policy_key, []).append(d)
    conflicts: list[dict[str, object]] = [
        {"policy_key": k, "documents": sorted(d.id for d in v), "reason": "multiple versions in effect"}
        for k, v in by_key.items()
        if len(v) > 1
    ]
    present_topics = {d.topic for d in docs}
    missing = [t for t in (filters.topics or []) if t not in present_topics]
    if not docs:
        return PolicySearchResult(
            query=query,
            effective_on=filters.effective_on,
            passages=[],
            applicable_documents=[],
            missing_topics=missing or list(filters.topics or []),
            conflicts=[],
        )

    doc_by_id = {d.id: d for d in docs}
    chunks = session.scalars(select(PolicyChunk).where(PolicyChunk.document_id.in_(doc_by_id))).all()
    ranks: dict[str, float] = dict(
        session.execute(
            text("SELECT id, ts_rank(tsv, to_tsquery('english', :q)) FROM policy_chunks WHERE document_id = ANY(:ids)"),
            {"q": _tsquery(query), "ids": list(doc_by_id)},
        ).all()
    )
    max_rank = max(ranks.values(), default=0.0) or 1.0
    qvec = embed([query])[0]
    scored = []
    for c in chunks:
        score = 0.6 * cosine(qvec, c.embedding) + 0.4 * (float(ranks.get(c.id, 0.0)) / max_rank)
        scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    passages = []
    for score, c in scored[:top_k]:
        d = doc_by_id[c.document_id]
        passages.append(
            PolicyPassage(
                chunk_id=c.id,
                document_id=d.id,
                policy_key=d.policy_key,
                version=d.version,
                title=d.title,
                section_ref=c.metadata_.get("section_ref", ""),
                section_title=c.metadata_.get("section_title", ""),
                content=c.content,
                score=round(score, 4),
                source_uri=d.source_uri,
            )
        )
    return PolicySearchResult(
        query=query,
        effective_on=filters.effective_on,
        passages=passages,
        applicable_documents=[
            {"document_id": d.id, "version": d.version, "title": d.title, "topic": d.topic}
            for d in sorted(docs, key=lambda d: d.id)
        ],
        missing_topics=missing,
        conflicts=conflicts,
    )


def document_sections(session: Session, document_id: str) -> list[PolicyPassage]:
    """All sections of one document (used to attach the governing policy's citation deterministically)."""
    doc = session.get(PolicyDocument, document_id)
    if doc is None:
        return []
    chunks = session.scalars(
        select(PolicyChunk).where(PolicyChunk.document_id == document_id).order_by(PolicyChunk.id)
    ).all()
    return [
        PolicyPassage(
            chunk_id=c.id,
            document_id=doc.id,
            policy_key=doc.policy_key,
            version=doc.version,
            title=doc.title,
            section_ref=c.metadata_.get("section_ref", ""),
            section_title=c.metadata_.get("section_title", ""),
            content=c.content,
            score=1.0,
            source_uri=doc.source_uri,
        )
        for c in chunks
    ]
