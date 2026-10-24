"""The typed contracts between the agents and the coordinator.

Agents answer in these shapes and nothing else; the coordinator validates every answer server-side before anything
is done with it. Identifiers an agent returns (a document, a page, a check) are untrusted: the coordinator checks each
against what the claim's evidence actually is, and a quote is accepted only if it is really on the page it cites.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Stance = Literal["supports", "contradicts", "missing", "unclear"]
Assessment = Literal["supported", "disputed", "unresolved"]


class Citation(BaseModel):
    document_id: int
    page: int = Field(ge=1)
    quote: str = Field(min_length=8, max_length=400)


class Finding(BaseModel):
    """One statement about the claim's evidence, with what it rests on. `check` names the pack check it bears on, when
    it bears on one; `stance` says whether the evidence supports the requirement, contradicts it, is missing, or is
    unclear."""
    requirement: str
    check: str | None = None
    stance: Stance
    statement: str = Field(min_length=3, max_length=300)
    citations: list[Citation] = Field(default_factory=list, max_length=4)


class EvidencePacket(BaseModel):
    """The investigator's answer: the claim's evidence, finding by finding, and what it could not settle."""
    findings: list[Finding] = Field(default_factory=list, max_length=12)
    open_questions: list[str] = Field(default_factory=list, max_length=5)


class Review(BaseModel):
    """The verifier's view of one of the investigator's findings."""
    finding: int = Field(ge=0)
    assessment: Assessment
    reason: str = Field(min_length=3, max_length=300)
    citations: list[Citation] = Field(default_factory=list, max_length=4)


class VerificationReport(BaseModel):
    """The verifier's answer: each finding supported, disputed or unresolved, and what the investigator missed
    (contradicting passages, missing evidence, a document of another process or an older version)."""
    reviews: list[Review] = Field(default_factory=list, max_length=12)
    additional: list[Finding] = Field(default_factory=list, max_length=6)


class Recommendation(BaseModel):
    """What the coordinator makes of the agents' work for one requirement: a proposal a person confirms (only for a
    check the rules could not decide), or a question routed to a person with both agents' views."""
    requirement: str
    check: str | None = None
    action: Literal["propose_verdict", "ask_person", "no_change"]
    verdict: Literal["PASS", "FAIL"] | None = None
    reason: str
    citations: list[Citation] = Field(default_factory=list)
    agreement: Literal["both", "investigator_only", "disputed", "unresolved", "rules"] = "both"
    proposal_id: int | None = None
    digest: str | None = None
