"""investigate_claim: a durable, bounded investigation of one claim by two agents, with a person's approval.

    start -> investigate -> verify -> reconcile -> await approval -> apply
             (investigator)  (verifier) (coordinator)  (a person)       (the domain service)

- **Two agents, two contexts.** The investigator builds a cited evidence packet with read-only tools; the verifier,
  with its own context and its own tools, checks each finding against the documents and looks for what the
  investigator missed (contradicting passages, missing evidence, another process's document, an older version). In
  "single" mode the verifier is skipped: the baseline the pair is measured against.
- **The coordinator is code, not a model.** It validates both answers against the evidence, reconciles them with each
  other and with the rules, and alone creates proposals, only for checks the rules could not decide. Agents never
  write a verdict or authorise anything.
- **Durable.** LangGraph checkpoints the run after every step (SQLite here, beside the store). A crash during agent
  work resumes from the last finished step (`recover`); a restart during human review resumes at the approval
  (`resume`). Steps that may run again are idempotent: the trace of an agent is rewritten whole, a proposal is found
  by its origin, and applying an approval is the atomic, stale-checked `agents.decide`.
- **Bounded.** Steps, tokens and a deadline per agent, a tool-call budget per agent, cancellation between steps.
- **Scoped.** A run belongs to a tenant; its checkpoint thread id is generated here and found only through the run,
  inside the tenant's session, so no caller names a thread to read or resume another tenant's work. Evidence that
  changed while the agents worked voids the run's recommendations (stale), and a person's approval is checked against
  the evidence again when it is applied.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import delete, func, select

from .. import agents
from ..engine import Engine
from ..store import AgentEvent, Claim, InvestigationRun, Proposal, RequirementState, utcnow
from . import agent as agent_loop
from .contracts import EvidencePacket, Recommendation, VerificationReport
from .coordinator import propose, reconcile
from .ledger import LedgeredModel
from .prompts import INVESTIGATOR, VERIFIER, investigator_task, verifier_task
from .tools import ToolGateway

FINAL = frozenset({"applied", "rejected", "no_change", "needs_person", "stale", "cancelled", "failed"})


class RunState(TypedDict, total=False):
    run_id: int
    tenant_id: int
    claim_id: int
    mode: str
    packet: dict | None
    report: dict | None
    recommendations: list
    decisions: list
    status: str


@dataclass
class Budgets:
    investigator: agent_loop.Budget = field(default_factory=lambda: agent_loop.Budget(steps=8))
    verifier: agent_loop.Budget = field(default_factory=lambda: agent_loop.Budget(steps=7))
    tool_calls: int = 10                   # per agent


def sqlite_checkpointer(path: str | Path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn), conn


class Investigations:
    """The workflow over one engine and one model. `checkpointer` persists the graph's state (see sqlite_checkpointer);
    the run, its trace and its outcome live in the store, per tenant."""

    def __init__(self, engine: Engine, model, checkpointer, budgets: Budgets | None = None):
        self.engine, self.model, self.budgets = engine, model, budgets or Budgets()
        self.graph = self._build().compile(checkpointer=checkpointer)

    # ------------------------------------------------------------------ the public surface
    def start(self, tenant_id: int, claim_id: int, requested_by: str, mode: str = "pair") -> dict:
        if mode not in ("pair", "single"):
            raise ValueError("mode is pair or single")
        with self.engine.store.tenant(tenant_id) as s:
            if s.get(Claim, claim_id) is None:
                raise KeyError(f"no claim {claim_id} for this tenant")
            run = InvestigationRun(claim_id=claim_id, thread_id=uuid.uuid4().hex, mode=mode, requested_by=requested_by)
            s.add(run)
            s.flush()
            run_id, thread = run.id, run.thread_id
        self.graph.invoke({"run_id": run_id, "tenant_id": tenant_id, "claim_id": claim_id, "mode": mode},
                          {"configurable": {"thread_id": thread}})
        return self.status(tenant_id, run_id)

    def resume(self, tenant_id: int, run_id: int, decisions: list[dict], by: str) -> dict:
        """A person's decisions on the run's proposals: [{"proposal": id, "approve": bool, "digest": "..."}]. `by` is
        the authenticated person; it is set on every decision, whatever the payload says."""
        run = self._run(tenant_id, run_id)
        if run["status"] != "awaiting_approval":
            raise ValueError(f"run {run_id} is {run['status']}, not waiting for approval")
        mine = {r["proposal_id"] for r in run["recommendations"] if r.get("proposal_id")}
        clean = []
        for d in decisions:
            if int(d["proposal"]) not in mine:
                raise ValueError(f"proposal {d['proposal']} is not one of run {run_id}'s")
            clean.append({"proposal": int(d["proposal"]), "approve": bool(d["approve"]), "digest": d.get("digest"),
                          "by": by})
        self.graph.invoke(Command(resume=clean), {"configurable": {"thread_id": run["thread_id"]}})
        return self.status(tenant_id, run_id)

    def recover(self, tenant_id: int, run_id: int) -> dict:
        """Continue a run whose process died, from its last finished step."""
        run = self._run(tenant_id, run_id)
        if run["status"] in FINAL or run["status"] == "awaiting_approval":
            return self.status(tenant_id, run_id)
        self.graph.invoke(None, {"configurable": {"thread_id": run["thread_id"]}})
        return self.status(tenant_id, run_id)

    def cancel(self, tenant_id: int, run_id: int, by: str) -> dict:
        """Stop a run: between agent steps if it is working, or now if it waits for approval (its proposals are
        declined with it)."""
        with self.engine.store.tenant(tenant_id) as s:
            run = s.get(InvestigationRun, run_id)
            if run is None:
                raise KeyError(f"no run {run_id} for this tenant")
            run.cancel_requested = True
            waiting = run.status == "awaiting_approval"
            pending = [r["proposal_id"] for r in run.recommendations or [] if r.get("proposal_id")]
            if waiting:
                run.status, run.updated_at = "cancelled", utcnow()
        if waiting:
            for pid in pending:
                try:
                    agents.decide(self.engine, tenant_id, pid, by, confirm=False)
                except ValueError:
                    pass                                   # already decided: nothing to decline
        return self.status(tenant_id, run_id)

    def status(self, tenant_id: int, run_id: int) -> dict:
        run = self._run(tenant_id, run_id)
        with self.engine.store.tenant(tenant_id) as s:
            events = s.scalars(select(AgentEvent).where(AgentEvent.run_id == run_id).order_by(AgentEvent.id)).all()
            run["trace"] = [{"role": e.role, "kind": e.kind, "step": e.step, "tool": e.tool, "args": e.args,
                             "outcome": e.outcome, "reason": e.reason, "detail": e.detail, "ms": e.ms}
                            for e in events]
        run.pop("thread_id", None)                         # never handed out
        return run

    def _run(self, tenant_id: int, run_id: int) -> dict:
        with self.engine.store.tenant(tenant_id) as s:
            run = s.get(InvestigationRun, run_id)
            if run is None:
                raise KeyError(f"no run {run_id} for this tenant")
            return {"run": run.id, "claim": run.claim_id, "mode": run.mode, "status": run.status,
                    "thread_id": run.thread_id, "requested_by": run.requested_by, "packet": run.packet,
                    "report": run.report, "recommendations": run.recommendations or [], "stats": run.stats or {},
                    "outcomes": run.outcomes or [], "tokens": run.tokens, "error": run.error,
                    "created_at": run.created_at.isoformat(timespec="seconds"),
                    "updated_at": run.updated_at.isoformat(timespec="seconds")}

    # ------------------------------------------------------------------ the graph
    def _build(self) -> StateGraph:
        g = StateGraph(RunState)
        g.add_node("start", self._start)
        g.add_node("investigate", self._investigate)
        g.add_node("verify", self._verify)
        g.add_node("reconcile", self._reconcile)
        g.add_node("await_approval", self._await_approval)
        g.add_node("apply", self._apply)
        g.add_edge(START, "start")
        g.add_edge("start", "investigate")
        g.add_conditional_edges("investigate", lambda st: "verify" if st.get("mode") == "pair" and st.get("packet")
                                and st.get("status") != "cancelled" else "reconcile", ["verify", "reconcile"])
        g.add_edge("verify", "reconcile")
        g.add_conditional_edges("reconcile", lambda st: "await_approval" if st.get("status") == "awaiting_approval"
                                else END, ["await_approval", END])
        g.add_edge("await_approval", "apply")
        g.add_edge("apply", END)
        return g

    def _start(self, st: RunState) -> dict:
        snap = self._snapshot(st)
        self._update(st, status="investigating", snapshot=snap)
        return {"status": "investigating"}

    def _investigate(self, st: RunState) -> dict:
        if self._cancelled(st):
            return self._update(st, status="cancelled")
        with self.engine.store.tenant(st["tenant_id"]) as s:
            claim = s.get(Claim, st["claim_id"])
            reqs = {rid: [c for c in r.checks if not (c in r.check_applies and not r.check_applies[c].matches(
                claim.tier, claim.claim_type))] for rid, r in self.engine.pack.requirements.items()
                if r.applies_to.matches(claim.tier, claim.claim_type)}
        result, events = self._agent(st, "investigator", INVESTIGATOR, investigator_task(st["claim_id"], reqs),
                                     EvidencePacket, self.budgets.investigator)
        self._record(st, "investigator", result, events)
        packet = result.output.model_dump() if result.output else None
        self._update(st, status="evidence_ready" if packet else "investigating", packet=packet)
        return {"packet": packet, "status": "evidence_ready" if packet else "investigation incomplete"}

    def _verify(self, st: RunState) -> dict:
        if self._cancelled(st):
            return self._update(st, status="cancelled")
        self._update(st, status="verifying")
        packet = EvidencePacket.model_validate(st["packet"])
        result, events = self._agent(st, "verifier", VERIFIER, verifier_task(st["claim_id"], packet),
                                     VerificationReport, self.budgets.verifier)
        self._record(st, "verifier", result, events)
        report = result.output.model_dump() if result.output else None
        self._update(st, report=report)
        return {"report": report, "status": "verification_ready" if report else "verification incomplete"}

    def _reconcile(self, st: RunState) -> dict:
        if self._cancelled(st):
            return self._update(st, status="cancelled")
        packet = EvidencePacket.model_validate(st["packet"]) if st.get("packet") else None
        report = VerificationReport.model_validate(st["report"]) if st.get("report") else None
        with self.engine.store.tenant(st["tenant_id"]) as s:
            then = s.get(InvestigationRun, st["run_id"]).snapshot or {}
        if self._snapshot(st) != then:
            # the evidence moved while the agents read it: what they found is about evidence that no longer is
            self._update(st, status="stale", error="the claim's evidence changed during the investigation; run it again")
            return {"status": "stale", "recommendations": []}
        recs, stats = reconcile(self.engine, st["tenant_id"], st["claim_id"], packet, report, st["mode"])
        recs = propose(self.engine, st["tenant_id"], st["run_id"], st["claim_id"], recs)
        if any(r.proposal_id for r in recs):
            status = "awaiting_approval"
        elif packet is None or any(r.action == "ask_person" for r in recs):
            status = "needs_person"
        else:
            status = "no_change"
        dumped = [r.model_dump() for r in recs]
        self._update(st, status=status, recommendations=dumped, stats=stats)
        return {"status": status, "recommendations": dumped}

    def _await_approval(self, st: RunState) -> dict:
        # runs again from the top when the graph resumes: nothing here may have an effect before interrupt()
        pending = [{"proposal": r["proposal_id"], "digest": r["digest"], "requirement": r["requirement"],
                    "check": r["check"], "verdict": r["verdict"], "reason": r["reason"]}
                   for r in st.get("recommendations", []) if r.get("proposal_id")]
        decisions = interrupt({"run_id": st["run_id"], "proposals": pending})
        return {"decisions": decisions}

    def _apply(self, st: RunState) -> dict:
        outcomes = []
        for d in st.get("decisions") or []:
            try:
                out = agents.decide(self.engine, st["tenant_id"], d["proposal"], d["by"], d["approve"],
                                    seen=d.get("digest"))
            except (KeyError, ValueError) as exc:            # already decided, not what was shown, gone
                out = f"refused: {exc}"
            outcomes.append({"proposal": d["proposal"], "approve": d["approve"], "by": d["by"], "outcome": out})
        if any(o["approve"] and not o["outcome"].startswith("refused") and o["outcome"] not in ("stale", "expired")
               for o in outcomes):
            status = "applied"
        elif any(o["outcome"] == "stale" for o in outcomes):
            status = "stale"
        else:
            status = "rejected"
        self._update(st, status=status, outcomes=outcomes)
        return {"status": status}

    # ------------------------------------------------------------------ helpers
    def _agent(self, st: RunState, role: str, system: str, task: str, contract, budget):
        gateway = ToolGateway(self.engine, st["tenant_id"], st["claim_id"], role, max_calls=self.budgets.tool_calls)
        if self.model is None:
            return agent_loop.AgentResult(None, "no model configured"), []
        model = LedgeredModel(self.engine, st["tenant_id"], self.model)
        result = agent_loop.run(model, gateway, system, task, contract, budget, cancelled=lambda: self._cancelled(st))
        return result, gateway.events

    def _record(self, st: RunState, role: str, result, events) -> None:
        """The agent's trace, written whole: a step that runs again replaces it rather than adding to it."""
        with self.engine.store.tenant(st["tenant_id"]) as s:
            s.execute(delete(AgentEvent).where(AgentEvent.run_id == st["run_id"], AgentEvent.role == role))
            for e in events:
                s.add(AgentEvent(run_id=st["run_id"], role=role, kind="tool", step=e.step, tool=e.tool,
                                 args=e.args, outcome=e.outcome, reason=e.reason,
                                 detail={"result_chars": e.result_chars}, ms=e.ms))
            s.add(AgentEvent(run_id=st["run_id"], role=role, kind="agent", step=result.steps, outcome=result.stop,
                             detail={"steps": result.steps, "prompt_tokens": result.prompt_tokens,
                                     "completion_tokens": result.completion_tokens, "cached_calls": result.cached_calls,
                                     "notes": result.notes}, ms=round(1000 * result.seconds, 1)))
            s.flush()
            run = s.get(InvestigationRun, st["run_id"])
            run.tokens = sum(int(e.detail.get("prompt_tokens", 0)) + int(e.detail.get("completion_tokens", 0))
                             for e in s.scalars(select(AgentEvent).where(AgentEvent.run_id == st["run_id"],
                                                                         AgentEvent.kind == "agent")))

    def _snapshot(self, st: RunState) -> dict:
        with self.engine.store.tenant(st["tenant_id"]) as s:
            return {r.requirement_id: r.evidence_digest for r in s.scalars(select(RequirementState).where(
                RequirementState.claim_id == st["claim_id"]))}

    def _cancelled(self, st: RunState) -> bool:
        with self.engine.store.tenant(st["tenant_id"]) as s:
            run = s.get(InvestigationRun, st["run_id"])
            return bool(run and run.cancel_requested)

    def _update(self, st: RunState, **values) -> dict:
        with self.engine.store.tenant(st["tenant_id"]) as s:
            run = s.get(InvestigationRun, st["run_id"])
            for k, v in values.items():
                setattr(run, k, v)
            run.updated_at = utcnow()
        return {k: v for k, v in values.items() if k == "status"}


def pending_runs(engine: Engine, tenant_id: int) -> list[dict]:
    """Runs waiting for a person, with what they propose."""
    with engine.store.tenant(tenant_id) as s:
        return [{"run": r.id, "claim": r.claim_id, "mode": r.mode, "proposals": [
            x for x in r.recommendations or [] if x.get("proposal_id")]}
            for r in s.scalars(select(InvestigationRun).where(InvestigationRun.status == "awaiting_approval")
                               .order_by(InvestigationRun.id))]


def proposals_of(engine: Engine, tenant_id: int, run_id: int) -> int:
    with engine.store.tenant(tenant_id) as s:
        return s.scalar(select(func.count()).select_from(Proposal).where(
            Proposal.origin.like(f"run:{run_id}:%"))) or 0


__all__ = ["Investigations", "Budgets", "sqlite_checkpointer", "Recommendation", "pending_runs", "proposals_of"]
