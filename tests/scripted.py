"""A scripted model for the investigator and verifier agents, and a claim the rules cannot decide but an agent can
read: shared by the workflow's tests and the service's."""
import json
import random
from datetime import datetime, time, timedelta

from sqlalchemy import select

from testamur.model import Status
from testamur.modeltier import Reply
from testamur.store import Claim, Creditor, RequirementState
from testamur.synth.render import DocContent, render_pdf

APPROVAL = "The board of the creditor approved this authority on 14 August 2026 by resolution 12 of 2026."


class Crash(BaseException):
    """A process dying mid-call: not an error an agent handles."""


class Agents:
    """A scripted model for both agents. Each script is a list of turns (dicts, or callables of the messages); the
    last turn repeats when the script runs out. The role is read from the system prompt, as the model would see it."""
    model_id, fingerprint = "scripted-agents", "scripted"

    def __init__(self, investigator, verifier=None, crash_on=None):
        self.scripts = {"investigator": investigator, "verifier": verifier or []}
        self.calls = {"investigator": 0, "verifier": 0}
        self.crash_on = crash_on

    def chat(self, messages, max_tokens=200, schema=None):
        role = "verifier" if messages[0]["content"].startswith("You are the verifier") else "investigator"
        n = self.calls[role]
        self.calls[role] += 1
        if self.crash_on == (role, n):
            raise Crash()
        script = self.scripts[role]
        turn = script[min(n, len(script) - 1)]
        turn = turn(messages) if callable(turn) else turn
        return Reply(json.dumps(turn), 120, 40)


def call(tool, **args):
    return {"note": f"look at {tool}", "action": "call_tool", "tool": tool, "args": args}


def finish(result):
    return {"note": "done", "action": "finish", "result": result}


def _conditional_authority(env):
    """Give a claim a new current authority that grants the vote only subject to its board's approval, and records the
    approval: the rules abstain on the vote (they cannot see an approval), an agent can read it."""
    world, store, engine = env
    with store.tenant(1) as s:
        st = next(x for x in s.scalars(select(RequirementState).where(RequirementState.requirement_id == "authority",
                                                                      RequirementState.status == Status.SATISFIED,
                                                                      RequirementState.via_trustee.is_(False))))
        e = s.get(Claim, st.claim_id)
        name = s.get(Creditor, e.creditor_id).legal_name
    page = [("title", "LETTER OF AUTHORITY"), ("kv", "Financial creditor", name),
            ("kv", "Authorised representative", "Meera Iyer, Head of Recoveries"),
            ("kv", "Powers", "To vote at meetings of the committee of creditors, subject to the prior approval of the "
                             "board"),
            ("kv", "Effective from", world.as_of.isoformat()),
            ("kv", "Valid until", (world.as_of + timedelta(days=365)).isoformat()),
            ("kv", "Date of issue", world.as_of.isoformat()), ("para", APPROVAL)]
    now = datetime.combine(world.as_of, time(10))
    out = engine.process(1, engine.ingest(1, e.matter_id, "authority.pdf", render_pdf(DocContent([page]),
                                                                                      random.Random(9)),
                                          "creditor_portal", now, declared_creditor_id=e.creditor_id))
    _, results = engine.verdicts(1, e.id, "authority")
    assert next(r for r in results if r.check_id == "auth_covers_voting").verdict == "ABSTAIN"
    return e.id, out.document_id


def _grant_finding(doc, stance="supports"):
    return {"requirement": "authority", "check": "auth_covers_voting", "stance": stance,
            "statement": "the vote is granted subject to board approval, and the letter records the approval",
            "citations": [{"document_id": doc, "page": 1, "quote": APPROVAL[:60]}]}


def _pair(doc, assessment="supported"):
    investigator = [call("claim_summary"), call("read_page", document_id=doc, page=1),
                    finish({"findings": [_grant_finding(doc)], "open_questions": []})]
    verifier = [call("read_page", document_id=doc, page=1),
                finish({"reviews": [{"finding": 0, "assessment": assessment, "reason": "the approval is on page 1",
                                     "citations": [{"document_id": doc, "page": 1, "quote": APPROVAL[:60]}]}],
                        "additional": []})]
    return investigator, verifier
