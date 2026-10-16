"""Validated output schemas for every agent.

Agent output is advisory. Every value here (order, amount, action) is checked
against records by deterministic code before anything happens.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["order_status", "refund_request", "damaged_item", "other"]


class Classification(BaseModel):
    category: Category
    order_reference: str | None = Field(default=None, description="Order number exactly as the customer wrote it")
    product_mentions: list[str] = Field(default_factory=list)
    damage_description: str | None = None
    photos_available: bool | None = None
    preference: Literal["refund", "replacement"] | None = None
    summary: str = Field(max_length=300)

    @field_validator("order_reference")
    @classmethod
    def _norm(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper().replace(" ", "")
        if v.isdigit():
            v = f"ORD-{v}"
        if v.startswith("ORD") and not v.startswith("ORD-"):
            v = "ORD-" + v[3:]
        return v or None


class EvidenceItem(BaseModel):
    ref: str = Field(description="Reference to the tool result, e.g. 'order:ORD-5001' or 'return:RET-ORD-5001'")
    statement: str


class EvidenceSummary(BaseModel):
    order_id: str
    facts: list[EvidenceItem] = Field(min_length=1)
    gaps: list[str] = Field(default_factory=list, description="Facts that could not be verified")
    summary: str


class Citation(BaseModel):
    document_id: str
    version: str
    section_ref: str
    quote: str = Field(max_length=600)


class PolicyFinding(BaseModel):
    citations: list[Citation] = Field(default_factory=list)
    interpretation: str
    applicable_version: str | None = None
    missing_policy: bool = False
    conflict: bool = False


ProposalAction = Literal["refund", "answer_only", "request_information", "escalate"]


class ResolutionProposal(BaseModel):
    """Structured resolution proposal from the resolution agent."""

    action: ProposalAction
    order_id: str | None
    amount_minor: int = Field(ge=0)
    currency: Literal["INR"] = "INR"
    reason: str = Field(max_length=1000)
    evidence_references: list[str] = Field(default_factory=list)
    policy_references: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class SingleAgentOutput(BaseModel):
    evidence: EvidenceSummary
    policy: PolicyFinding
    proposal: ResolutionProposal


class CustomerResponse(BaseModel):
    message: str = Field(min_length=10, max_length=2000)
