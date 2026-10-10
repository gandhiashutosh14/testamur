"""The vocabulary every module shares: verdicts, the cost ladder, requirement statuses and the records that carry
evidence between stages. Plain data, no behaviour beyond small helpers."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Verdict(StrEnum):
    PASS = "PASS"          # the evidence shows the requirement is met
    FAIL = "FAIL"          # the evidence shows it is not met
    ABSTAIN = "ABSTAIN"    # the engine cannot decide; a person must look. Never rounded up to PASS
    SKIPPED = "SKIPPED"    # not evaluated because the outcome was already decided; never counts as PASS


class Severity(StrEnum):
    CRITICAL = "critical"  # a FAIL means the requirement is not satisfied
    MAJOR = "major"        # a FAIL is a finding someone must act on
    MINOR = "minor"        # a FAIL is a finding worth recording


class Tier(StrEnum):
    """The cost ladder. A check runs at the cheapest tier that can decide it."""
    METADATA = "metadata"  # facts known without reading the document: upload channel, dates, the claim record
    FIELD = "field"        # a deterministic rule over a value read from the page
    MODEL = "model"        # a language-model judgement over located text; must quote its evidence and may abstain


TIER_ORDER = {Tier.METADATA: 0, Tier.FIELD: 1, Tier.MODEL: 2}


class Status(StrEnum):
    """Where a requirement stands for one creditor claim."""
    SATISFIED = "SATISFIED"
    SATISFIED_WITH_FINDINGS = "SATISFIED_WITH_FINDINGS"
    NOT_SATISFIED = "NOT_SATISFIED"
    EXPIRED = "EXPIRED"
    COVERED_BY_TRUSTEE = "COVERED_BY_TRUSTEE"
    VERIFIED_OUT_OF_BAND = "VERIFIED_OUT_OF_BAND"
    NOT_REQUIRED = "NOT_REQUIRED"
    MISSING = "MISSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# Statuses in which the platform vouches for the creditor. Reporting one of these when the truth is otherwise is
# the dangerous error: the committee of creditors will rely on it.
VOUCHING = frozenset({Status.SATISFIED, Status.SATISFIED_WITH_FINDINGS, Status.COVERED_BY_TRUSTEE,
                      Status.VERIFIED_OUT_OF_BAND, Status.NOT_REQUIRED})


class Line(BaseModel):
    text: str
    page: int
    bbox: tuple[float, float, float, float] | None = None
    conf: float = 1.0


class Page(BaseModel):
    number: int
    lines: list[Line]
    width: float | None = None
    height: float | None = None


class Extraction(BaseModel):
    pages: list[Page]
    method: str                     # "text_layer" or "ocr:<engine>"
    engine_version: str
    ocr_pages: int = 0              # pages that went through OCR (the paid step)
    seconds: float = 0.0

    def text(self, page: int | None = None) -> str:
        pages = self.pages if page is None else [p for p in self.pages if p.number == page]
        return "\n".join(line.text for p in pages for line in p.lines)


class FieldValue(BaseModel):
    name: str
    value: Any
    raw: str
    page: int | None = None
    line: str
    conf: float = 1.0
    source: str | None = None       # "reviewer" when a person supplied the reading


class Evidence(BaseModel):
    document_id: int | None = None
    page: int | None = None
    quote: str | None = None
    source: str = "text"            # text | ocr | metadata | model | record


class CheckResult(BaseModel):
    check_id: str
    requirement_id: str
    verdict: Verdict
    severity: Severity
    tier: Tier
    reason: str
    evidence: list[Evidence] = Field(default_factory=list)
    fix_hint: str | None = None
    transient: bool = False         # an infrastructure failure (timeout, provider error), not a judgement
    lapsed: bool = False            # the failure is an expiry that has already passed on the as-of date
    cost: dict[str, float] = Field(default_factory=dict)


class UploadMeta(BaseModel):
    """What is known about a file before anyone reads it."""
    upload_id: int
    tenant_id: int
    matter_id: int
    filename: str
    channel: str                    # staff_upload | creditor_portal | email_ingest | staff_verified
    uploaded_at: datetime
    declared_creditor_id: int | None = None


def money(value: str | int | float | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def days_between(a: date, b: date) -> int:
    return (b - a).days
