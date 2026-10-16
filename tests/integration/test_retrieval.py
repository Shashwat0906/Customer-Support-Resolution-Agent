"""Policy ingestion and retrieval."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from app.db.models import PolicyChunk, PolicyDocument
from app.db.session import session_scope
from app.policies.retrieval import PolicyFilters, search_policy
from scripts.ingest_policies import ingest


def test_ingestion_is_idempotent_and_complete(anchor: datetime) -> None:
    ingest()
    ingest()
    with session_scope() as s:
        docs = s.scalars(select(PolicyDocument)).all()
        topics = {d.topic for d in docs}
        assert topics == {"refunds", "returns", "damage", "shipping", "exceptions"}
        assert {d.id for d in docs if d.policy_key == "refund-eligibility"} == {
            "refund-eligibility-v1",
            "refund-eligibility-v2",
        }
        assert all(d.source_uri and d.content_hash and d.effective_from for d in docs)
        chunks = s.scalar(select(func.count()).select_from(PolicyChunk))
        sample = s.scalar(select(PolicyChunk).where(PolicyChunk.id == "refund-eligibility-v2#s3"))
    assert chunks == 26
    assert sample.metadata_["section_ref"] == "§3" and len(sample.embedding) == 256


def test_retrieval_filters_to_version_in_effect(anchor: datetime) -> None:
    with session_scope() as s:
        new = search_policy(
            s, "refund eligibility after inspection", PolicyFilters(topics=["refunds"], effective_on=date(2026, 10, 1))
        )
        old = search_policy(
            s, "refund eligibility after inspection", PolicyFilters(topics=["refunds"], effective_on=date(2026, 3, 6))
        )
    assert {p.document_id for p in new.passages} == {"refund-eligibility-v2"}
    assert {p.document_id for p in old.passages} == {"refund-eligibility-v1"}
    assert new.passages[0].section_ref == "§3"  # the "when a refund becomes eligible" section ranks first
    assert all(p.untrusted for p in new.passages)


def test_retrieval_reports_missing_topics(anchor: datetime) -> None:
    with session_scope() as s:
        r = search_policy(s, "refund", PolicyFilters(topics=["refunds", "damage"], effective_on=date(2023, 11, 14)))
    assert r.passages == [] and set(r.missing_topics) == {"refunds", "damage"}


def test_relevant_sections_rank_highly(anchor: datetime) -> None:
    cases = [
        ("photos damage description required", "damage", "damaged-products-v1", "§2"),
        ("shipment more than 3 days late escalate", "shipping", "shipping-delays-v1", "§3"),
        ("when is the refund credited to my card", "returns", "return-processing-v1", "§4"),
    ]
    with session_scope() as s:
        for query, topic, doc, section in cases:
            r = search_policy(s, query, PolicyFilters(topics=[topic], effective_on=date(2026, 10, 1)), top_k=2)
            assert (doc, section) in {(p.document_id, p.section_ref) for p in r.passages}, (query, r.passages)
