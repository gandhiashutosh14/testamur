"""Load and validate a domain pack. The pack is the contract between domain experts and the engine: adding a document
type or tightening a check is a reviewed change to YAML, not to code."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from .model import Severity, Tier

PACKS = Path(__file__).parent / "packs"

# The pack tables each rule reads. A person's decision about a check is bound to the check's definition and to these
# tables (see Pack.rule_digest): change either and the decision stops applying, because the check no longer means
# what the person decided about.
RULE_TABLES = {"money_at_least": ("tier_minimum_liability",), "in_allowlist": ("accredited_bodies",),
               "scope_mentions": ("service_terms",)}


class FieldSpec(BaseModel):
    labels: list[str]
    type: str                                   # text | money | date | list


class DocTypeSpec(BaseModel):
    title_keywords: list[str]
    filename_aliases: list[str] = Field(default_factory=list)
    key_labels: list[str] = Field(default_factory=list)
    fields: dict[str, FieldSpec] = Field(default_factory=dict)


class CheckSpec(BaseModel):
    tier: Tier
    severity: Severity
    rule: str
    params: dict[str, Any] = Field(default_factory=dict)
    hint: str = ""


class Applicability(BaseModel):
    tiers: list[str] | None = None
    engagement_types: list[str] | None = None

    def matches(self, tier: str, engagement_type: str) -> bool:
        return (self.tiers is None or tier in self.tiers) and \
               (self.engagement_types is None or engagement_type in self.engagement_types)


class RequirementSpec(BaseModel):
    doc_type: str
    applies_to: Applicability = Field(default_factory=Applicability)
    checks: list[str]
    check_applies: dict[str, Applicability] = Field(default_factory=dict)
    group_cover: bool = False


class Pack(BaseModel):
    name: str
    version: int
    tier_minimum_liability: dict[str, int]
    accredited_bodies: list[str]
    doc_types: dict[str, DocTypeSpec]
    checks: dict[str, CheckSpec]
    requirements: dict[str, RequirementSpec]
    service_terms: dict[str, list[str]]
    injection_markers: list[str]
    digest: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Pack:
        for rid, req in self.requirements.items():
            if req.doc_type not in self.doc_types:
                raise ValueError(f"requirement {rid}: unknown doc_type {req.doc_type}")
            for cid in req.checks:
                if cid not in self.checks:
                    raise ValueError(f"requirement {rid}: unknown check {cid}")
            for cid in req.check_applies:
                if cid not in req.checks:
                    raise ValueError(f"requirement {rid}: check_applies names {cid}, which it does not run")
        for cid, chk in self.checks.items():
            if chk.tier == Tier.MODEL and not {"claim", "counterclaim", "search"} <= set(chk.params):
                raise ValueError(f"check {cid}: a model-tier check needs claim, counterclaim and search")
        for marker in self.injection_markers:
            re.compile(marker)
        return self

    def injection_patterns(self) -> list[re.Pattern[str]]:
        return [re.compile(m, re.I) for m in self.injection_markers]

    def rule_digest(self, check_id: str) -> str:
        """What a check means: its definition (not its wording for people) and the pack tables its rule reads."""
        spec = self.checks[check_id]
        tables = {t: getattr(self, t) for t in RULE_TABLES.get(spec.rule, ())}
        raw = json.dumps({"check": spec.model_dump(mode="json", exclude={"hint"}), "tables": tables}, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=8)
def load_pack(name: str = "supplier_assurance") -> Pack:
    raw = (PACKS / name / "pack.yaml").read_bytes()
    pack = Pack.model_validate(yaml.safe_load(raw))
    pack.digest = hashlib.sha256(raw).hexdigest()[:12]
    return pack
