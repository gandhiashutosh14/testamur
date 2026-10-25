"""The coordinator: deterministic code between the agents and anything that changes.

It validates every answer against the evidence (a citation must name this claim's document, a real page, and words
that are really on it; a check must be one the requirement runs), reconciles the two agents with each other and with
the rules, and alone turns the result into something a person acts on. The rules keep the last word on what they
decided: the agents can propose a verdict only for a check the rules could not decide (an ABSTAIN), and an agent
that disputes a rule's verdict sends the question to a person, never a proposal to overturn it. A proposal is bound to
the evidence it was made on (agents.propose) and idempotent per run, so a re-executed step does not propose twice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from .. import agents
from ..engine import Engine
from ..model import Verdict
from ..modeltier import quote_holds
from ..store import Proposal
from .contracts import Citation, EvidencePacket, Finding, Recommendation, VerificationReport
from .tools import ToolGateway


@dataclass
class Checked:
    """An agent's answer after validation: what stands (with its index in the answer), and what was dropped and why."""
    findings: list[tuple[int, Finding]] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    citations: int = 0
    citations_valid: int = 0


def validate_findings(engine: Engine, tenant_id: int, claim_id: int, findings: list[Finding]) -> Checked:
    out = Checked()
    gateway = ToolGateway(engine, tenant_id, claim_id, "verifier", max_calls=10_000)   # the scope, not a budget
    with engine.store.tenant(tenant_id) as s:
        _, _, docs = gateway._scope(s)
        for index, f in enumerate(findings):
            req = engine.pack.requirements.get(f.requirement)
            if req is None:
                out.dropped.append({"statement": f.statement, "why": f"no requirement {f.requirement!r}"})
                continue
            if f.check is not None and f.check not in req.checks:
                out.dropped.append({"statement": f.statement, "why": f"{f.requirement} runs no check {f.check!r}"})
                continue
            good = []
            for c in f.citations:
                out.citations += 1
                why = _citation_problem(engine, s, tenant_id, docs, c)
                if why is None:
                    good.append(c)
                    out.citations_valid += 1
            if not good and f.stance in ("supports", "contradicts"):
                out.dropped.append({"statement": f.statement, "why": "no citation could be verified",
                                    "requirement": f.requirement, "check": f.check, "stance": f.stance})
                continue
            out.findings.append((index, f.model_copy(update={"citations": good})))
    return out


def _citation_problem(engine: Engine, s, tenant_id: int, docs: dict, c: Citation) -> str | None:
    doc = docs.get(c.document_id)
    if doc is None:
        return f"document {c.document_id} is not this claim's evidence"
    if doc.hostile:
        return f"document {c.document_id} is quarantined"
    pages = engine._extraction(s, tenant_id, doc.sha256).pages
    page = next((p for p in pages if p.number == c.page), None)
    if page is None:
        return f"document {c.document_id} has no page {c.page}"
    text = "\n".join(line.text for line in page.lines)
    if not any(ch.isalnum() for ch in c.quote):
        return "the quote is empty"
    if not quote_holds(c.quote, text):
        return "the quote is not on that page"
    return None


def reconcile(engine: Engine, tenant_id: int, claim_id: int, packet: EvidencePacket | None,
              report: VerificationReport | None, mode: str) -> tuple[list[Recommendation], dict]:
    """What the agents' work comes to, requirement by requirement. `mode`: "single" (the investigator alone) or
    "pair" (the investigator's findings stand only where the verifier supports them)."""
    found = validate_findings(engine, tenant_id, claim_id, packet.findings if packet else [])
    reviews: dict[int, object] = {}
    extra = Checked()
    review_cites = [0, 0]
    if report is not None:
        reviews = {r.finding: r for r in report.reviews if 0 <= r.finding < len(packet.findings if packet else [])}
        extra = validate_findings(engine, tenant_id, claim_id, report.additional)
        gateway = ToolGateway(engine, tenant_id, claim_id, "verifier", max_calls=10_000)
        with engine.store.tenant(tenant_id) as s:
            _, _, docs = gateway._scope(s)
            for i, r in list(reviews.items()):
                good = [c for c in r.citations if _citation_problem(engine, s, tenant_id, docs, c) is None]
                review_cites[0] += len(r.citations)
                review_cites[1] += len(good)
                reviews[i] = r.model_copy(update={"citations": good})
    recs: list[Recommendation] = []
    with engine.store.tenant(tenant_id) as s:
        for i, f in found.findings:
            st, results = engine._verdicts(s, tenant_id, claim_id, f.requirement)
            verdicts = {r.check_id: r for r in results}
            review = reviews.get(i)
            # the verifier's support is a second reading only with a citation of its own that holds (its citations
            # are the verified ones by now); support it could not cite leaves the finding unresolved
            backed = review is not None and review.assessment == "supported" and bool(review.citations)
            agreement = "both" if backed else (
                "investigator_only" if mode == "single" else
                ("disputed" if review and review.assessment == "disputed" else "unresolved"))
            if f.check is None or f.stance in ("missing", "unclear"):
                if f.stance in ("contradicts", "missing") and (st is None or st.status in _VOUCHING):
                    recs.append(Recommendation(requirement=f.requirement, check=f.check, action="ask_person",
                                               reason=f"the agents found something the rules did not: {f.statement}",
                                               citations=f.citations, agreement=agreement))
                continue
            engine_v = verdicts.get(f.check)
            agent_v = Verdict.PASS if f.stance == "supports" else Verdict.FAIL
            if engine_v is not None and engine_v.verdict in (Verdict.PASS, Verdict.FAIL):
                if engine_v.verdict != agent_v:
                    recs.append(Recommendation(requirement=f.requirement, check=f.check, action="ask_person",
                                               reason=f"an agent disputes the rule's {engine_v.verdict} on {f.check}: "
                                                      f"{f.statement}", citations=f.citations, agreement="rules"))
                else:
                    recs.append(Recommendation(requirement=f.requirement, check=f.check, action="no_change",
                                               reason=f"agrees with the rule's {engine_v.verdict}",
                                               citations=f.citations, agreement=agreement))
                continue
            if engine_v is None or engine_v.verdict != Verdict.ABSTAIN:
                continue                                   # skipped, or not evaluated: nothing to decide
            if agreement in ("both", "investigator_only"):
                recs.append(Recommendation(requirement=f.requirement, check=f.check, action="propose_verdict",
                                           verdict=agent_v.value, reason=f.statement, citations=f.citations,
                                           agreement=agreement))
            else:
                why = ("the verifier did not assess it" if review is None else
                       "it agreed without a citation that could be verified" if review.assessment == "supported" else
                       review.reason)
                recs.append(Recommendation(requirement=f.requirement, check=f.check, action="ask_person",
                                           reason=f"the agents disagree on {f.check} ({agreement}): {f.statement}; "
                                                  f"verifier: {why}", citations=f.citations, agreement=agreement))
        for _, f in extra.findings:
            if f.stance in ("contradicts", "missing"):
                recs.append(Recommendation(requirement=f.requirement, check=f.check, action="ask_person",
                                           reason=f"the verifier found: {f.statement}", citations=f.citations,
                                           agreement="unresolved"))
        # a problem an agent reported but could not cite is not evidence; it may still be a problem, so a person
        # looks (a claim of support that could not be cited is simply dropped)
        for d in found.dropped + extra.dropped:
            if d.get("stance") == "contradicts" and d.get("requirement") in engine.pack.requirements:
                recs.append(Recommendation(requirement=d["requirement"], check=d.get("check"), action="ask_person",
                                           reason=f"an agent reported, without a citation that could be verified: "
                                                  f"{d['statement']}", agreement="unresolved"))
    stats = {"findings": len(packet.findings) if packet else 0, "findings_kept": len(found.findings),
             "dropped": found.dropped + extra.dropped,
             "citations": found.citations + extra.citations + review_cites[0],
             "citations_valid": found.citations_valid + extra.citations_valid + review_cites[1],
             "reviews": len(reviews), "additional": len(extra.findings)}
    return _dedup(recs), stats


_VOUCHING = {"SATISFIED", "SATISFIED_WITH_FINDINGS", "COVERED_BY_TRUSTEE", "VERIFIED_OUT_OF_BAND", "NOT_REQUIRED"}


def _dedup(recs: list[Recommendation]) -> list[Recommendation]:
    """One recommendation per (requirement, check, action, verdict): the first, with the citations of all."""
    out: dict[tuple, Recommendation] = {}
    for r in recs:
        key = (r.requirement, r.check, r.action, r.verdict)
        if key in out:
            seen = {(c.document_id, c.page, c.quote) for c in out[key].citations}
            out[key].citations += [c for c in r.citations if (c.document_id, c.page, c.quote) not in seen]
        else:
            out[key] = r
    # two proposals for one check that point opposite ways are not a proposal: a person decides
    for (rid, cid, action, verdict), r in list(out.items()):
        if action == "propose_verdict" and (rid, cid, action, "PASS" if verdict == "FAIL" else "FAIL") in out:
            out[(rid, cid, action, verdict)] = r.model_copy(update={"action": "ask_person", "verdict": None,
                                                                     "agreement": "disputed",
                                                                     "reason": "findings point both ways"})
    return list(out.values())


def propose(engine: Engine, tenant_id: int, run_id: int, claim_id: int,
            recs: list[Recommendation]) -> list[Recommendation]:
    """Turn the coordinator's verdict recommendations into proposals a person confirms. Idempotent per run: a step
    executed again finds its proposal by origin and does not make a second one."""
    out = []
    agent = f"investigation-{run_id}"
    for r in recs:
        if r.action != "propose_verdict":
            out.append(r)
            continue
        origin = f"run:{run_id}:{r.requirement}:{r.check}"
        with engine.store.tenant(tenant_id) as s:
            existing = s.scalar(select(Proposal).where(Proposal.origin == origin))
            found = (existing.id, existing.binding_digest) if existing is not None else None
        if found is None:
            p = agents.propose(engine, tenant_id, agent, "check_verdict",
                               {"claim": claim_id, "requirement": r.requirement, "check": r.check,
                                "verdict": r.verdict}, f"{r.reason} ({r.agreement})", origin=origin)
            found = (p["proposal"], p["digest"])
        out.append(r.model_copy(update={"proposal_id": found[0], "digest": found[1]}))
    return out
