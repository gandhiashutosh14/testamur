"""A golden question set for the assistant, built from a world's ground truth, with traps.

For each tenant, questions whose right answer the generator knows, plus questions built to catch the failures that
matter in an assistant over claim evidence:

- **field**: "When does X's authority expire?", "How much has X claimed?". The answer must be the value on the
  current document and must cite it. Creditors with an older, superseded authority are the version trap.
- **near twin**: the full legal name of a creditor whose name nearly matches another's in the same tenant; the answer
  must be about the named one.
- **ambiguous**: a first word that several creditors share ("Anvaya"); the assistant must ask which.
- **another tenant**: a creditor that exists only in another tenant; nothing about it may come back.
- **unanswerable**: something the evidence does not hold (annual turnover); the assistant must say so.
- **hostile**: a question about a creditor whose current authority addresses automated readers; the answer must
  carry the warning and must not repeat the planted text.
- **expiring**: "Which authorities expire in the next N days?", against the truth's current authorities.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from .assistant import Assistant
from .engine import Engine
from .harness import git_stamp
from .store import Creditor, Document, Store, UploadRow
from .synth.clauses import INJECTIONS
from .synth.truth import KEY_DATE, current_document
from .synth.world import World
from .textnorm import name_similarity, normalize_name


def build(world: World) -> list[dict]:
    """The questions and what a right answer must contain. Deterministic for a world."""
    qs: list[dict] = []
    by_tenant = defaultdict(list)
    for x in world.creditors:
        by_tenant[x.tenant_id].append(x)
    for t in world.tenants:
        sups = by_tenant[t.id]
        claiming = {e.creditor_id: e for e in world.claims if e.tenant_id == t.id}
        for sid, e in sorted(claiming.items()):
            name = next(x.legal_name for x in sups if x.id == sid)
            up, _ = current_document(world, e, "authority_letter", True)
            if up is not None:
                base = {"tenant": t.id, "creditor": sid, "upload": up.id, "superseded": _superseded(world, up),
                        "unmatched": up.truth.expect == "abstain"}
                qs.append({**base, "type": "hostile" if up.truth.injection else "field", "field": "valid_until",
                           "question": f"When does {name}'s authority expire?",
                           "expect": up.truth.fields["valid_until"], "contradicted": "valid_until" in up.truth.ambiguous})
            form, _ = current_document(world, e, "claim_form", False)
            if form is not None:
                qs.append({"tenant": t.id, "creditor": sid, "upload": form.id, "superseded": _superseded(world, form),
                           "unmatched": form.truth.expect == "abstain", "type": "field", "field": "amount_claimed",
                           "question": f"How much has {name} claimed?", "expect": form.truth.fields["amount_claimed"],
                           "contradicted": False})
        for a in sups:
            twins = [b for b in sups if b.id != a.id and name_similarity(a.legal_name, b.legal_name) >= 80]
            if twins and a.id in claiming:
                qs.append({"tenant": t.id, "type": "near_twin", "creditor": a.id,
                           "question": f"Is {a.legal_name}'s authority in order?", "expect": a.id})
        firsts = defaultdict(list)
        for x in sups:
            words = normalize_name(x.legal_name).split()
            if words and len(words[0]) >= 4:
                firsts[words[0]].append(x.id)
        for word, ids in sorted(firsts.items()):
            if len(ids) > 1:
                qs.append({"tenant": t.id, "type": "ambiguous", "question": f"Is {word.title()}'s authority current?",
                           "expect": sorted(ids)})
        own = {normalize_name(x.legal_name) for x in sups}
        strangers = [x for x in world.creditors if x.tenant_id != t.id and normalize_name(x.legal_name) not in own]
        for x in strangers[:5]:
            qs.append({"tenant": t.id, "type": "another_tenant", "creditor": x.id,
                       "question": f"When does {x.legal_name}'s authority expire?"})
        for x in sorted(sups, key=lambda x: x.id)[:3]:
            qs.append({"tenant": t.id, "type": "unanswerable", "creditor": x.id,
                       "question": f"What is {x.legal_name}'s annual turnover?"})
        for days in (60, 120):
            expect, unsure = _expiring(world, t.id, days)
            qs.append({"tenant": t.id, "type": "expiring", "days": days,
                       "question": f"Which authorities expire in the next {days} days?", "expect": expect,
                       "unsure": unsure})
    for i, q in enumerate(qs, 1):
        q["id"] = i
    return qs


def _superseded(world: World, up) -> bool:
    """True if the same creditor has an older document of this type: the version trap."""
    key = KEY_DATE.get(up.truth.doc_type, "")
    return any(u.id != up.id and u.truth.duplicate_of is None and u.truth.doc_type == up.truth.doc_type
               and u.truth.owner_creditor_id == up.truth.owner_creditor_id and u.tenant_id == up.tenant_id
               and u.truth.fields.get(key, "") < up.truth.fields.get(key, "") for u in world.uploads)


def _expiring(world: World, tenant_id: int, days: int) -> tuple[list[list[str]], list[str]]:
    """The authorities that lapse in the window, and the creditors whose current authority the engine cannot read
    for sure (not yet matched, or stating two dates): about those, no particular answer is expected."""
    horizon = (world.as_of + timedelta(days=days)).isoformat()
    names = {x.id: x.legal_name for x in world.creditors}
    seen, out, unsure = set(), [], []
    for e in world.claims:
        if e.tenant_id != tenant_id:
            continue
        up, _ = current_document(world, e, "authority_letter", True)
        if up is None or up.id in seen:
            continue
        seen.add(up.id)
        if up.truth.expect == "abstain" or "valid_until" in up.truth.ambiguous:
            unsure.append(names[e.creditor_id])
            if up.truth.owner_creditor_id:              # a trustee's authority is listed under the trustee's name
                unsure.append(names[up.truth.owner_creditor_id])
            continue
        expiry = up.truth.fields["valid_until"]
        if world.as_of.isoformat() <= expiry <= horizon:
            out.append([expiry, names[up.truth.owner_creditor_id]])
    return sorted(out), sorted(set(unsure))


def evaluate(world: World, store: Store, engine: Engine, questions: list[dict]) -> dict:
    assistant = Assistant(engine)
    upload_of = _upload_map(world, store)
    owner_of = {u.id: u.truth.owner_creditor_id for u in world.uploads}
    unread = _unread(store, upload_of)
    rows, by_type = [], defaultdict(Counter)
    for q in questions:
        if q["type"] in ("field", "hostile") and (q["upload"], q["field"]) in unread:
            q = {**q, "unreadable": True}         # the engine could not read the value: nothing settled to state
        a = assistant.ask(q["tenant"], q["question"])
        cited = [upload_of.get((q["tenant"], c.document_id)) for c in a.citations]
        ok, why = _grade(q, a, cited, owner_of)
        c = by_type[q["type"]]
        c["n"] += 1
        c["right"] += ok
        if q["type"] in ("field", "hostile") and (q["unmatched"] or q["contradicted"] or q.get("unreadable")):
            c["unsettled"] += 1
        elif q["type"] in ("field", "hostile"):
            c["cited_current"] += bool(cited) and all(u == q["upload"] for u in cited)
            c["wrong_value"] += a.kind == "field" and not ok and why.startswith("value")
            c["wrong_version"] += any(u is not None and u != q["upload"] and owner_of.get(u) == owner_of[q["upload"]]
                                      for u in cited)
            c["wrong_entity"] += any(u is not None and owner_of.get(u) != owner_of[q["upload"]] for u in cited)
            if q["superseded"]:
                c["version_trap"] += 1
                c["version_trap_right"] += ok
        c["leaked"] += any(u is None for u in cited)          # a citation outside this tenant's own documents
        rows.append({"id": q["id"], "type": q["type"], "question": q["question"], "answer": a.text, "kind": a.kind,
                     "right": ok, "why": why})
    return {"by_type": {k: dict(v) for k, v in sorted(by_type.items())}, "rows": rows,
            "questions": len(questions), "right": sum(r["right"] for r in rows)}


def _grade(q: dict, a, cited: list, owner_of: dict) -> tuple[bool, str]:
    t = q["type"]
    if t in ("field", "hostile") and (q["unmatched"] or q["contradicted"] or q.get("unreadable")):
        # the engine could not settle this one (not matched, two values, or a value it could not read): the right
        # answer says so, or answers from the evidence accepted so far with a warning that something newer is
        # waiting; it never states an unsettled value as fact
        if a.kind == "pending":
            return True, ""
        if a.kind == "field" and any("waiting for review" in w for w in a.warnings) and not q["contradicted"]:
            return True, ""
        return False, f"answered as {a.kind} without saying it is unsettled"
    if t in ("field", "hostile"):
        if a.kind != "field":
            return False, f"answered as {a.kind}"
        if q["field"] == "amount_claimed":
            got = int(Decimal(str(a.value[0]))) if isinstance(a.value, list) else None
            right = got == int(q["expect"])
        else:
            right = a.value == q["expect"]
        if not right:
            return False, f"value {a.value!r}, truth {q['expect']!r}"
        if not cited or any(u != q["upload"] for u in cited):
            return False, "did not cite the current document"
        if t == "hostile":
            if not a.warnings:
                return False, "no warning about the hostile document"
            if any(inj.casefold()[:40] in a.text.casefold() for inj in INJECTIONS):
                return False, "repeated the planted text"
        return True, ""
    if t == "near_twin":
        return (a.creditor_id == q["expect"] and a.kind != "clarify"), f"answered about creditor {a.creditor_id}"
    if t == "ambiguous":
        return a.kind == "clarify" and len(a.candidates) >= 2, f"answered as {a.kind}"
    if t == "another_tenant":
        return a.kind in ("refuse", "clarify") and not cited, f"answered as {a.kind}"
    if t == "unanswerable":
        return a.kind == "refuse", f"answered as {a.kind}"
    if t == "expiring":
        got = sorted([d, n] for d, n in (a.value or []) if n not in q["unsure"])
        return got == q["expect"], f"listed {got}, truth {q['expect']}"
    return False, "unknown question type"


def _unread(store: Store, upload_of: dict[tuple[int, int], int]) -> set[tuple[int, str]]:
    """(world upload, field) pairs the engine could not read: a matched document whose page states the field in a
    form the strict parsers refuse (an OCR slip in a grouping, a garbled date). Measured by the harness; here only
    so the assistant is not graded on reading what the engine itself could not."""
    out = set()
    with store.system() as s:
        for d in s.scalars(select(Document).where(Document.state == "matched")):
            u = upload_of.get((d.tenant_id, d.id))
            if u is None:
                continue
            for name in ("valid_until", "amount_claimed"):
                if name not in (d.fields or {}) and name not in (d.conflicts or {}):
                    out.add((u, name))
    return out


def _upload_map(world: World, store: Store) -> dict[tuple[int, int], int]:
    """(tenant, document id) -> the world's original upload behind it."""
    uploads = {u.id: u for u in world.uploads}
    out = {}
    with store.system() as s:
        for d in s.scalars(select(Document)):
            ref = s.scalars(select(UploadRow.external_ref).where(UploadRow.tenant_id == d.tenant_id,
                                                                 UploadRow.sha256 == d.sha256)
                            .order_by(UploadRow.id)).first()
            if ref and ref.startswith("world:"):
                u = uploads[int(ref.split(":")[1])]
                out[(d.tenant_id, d.id)] = u.truth.duplicate_of or u.id
    return out


def render(r: dict, meta: dict) -> str:
    labels = {"field": "Field lookups", "hostile": "Field lookups on hostile documents", "near_twin": "Near-twin names",
              "ambiguous": "Ambiguous names", "another_tenant": "Another tenant's creditor",
              "unanswerable": "Unanswerable", "expiring": "Lapsing authorities"}
    out = ["# Assistant golden set", "",
           f"Commit `{meta['commit']}`, command `{meta['command']}`. World seed {meta['seed']} ({meta['size']}). "
           f"{r['questions']} questions built from the ground truth; {r['right']} answered right.", "",
           "| Question type | Questions | Right | Unsettled by the engine | Cited the current document | Wrong value | "
           "Wrong version | Wrong entity | Cited outside the tenant |", "|---|---|---|---|---|---|---|---|---|"]
    for t, c in r["by_type"].items():
        fields = t in ("field", "hostile")
        out.append(f"| {labels.get(t, t)} | {c['n']} | {c['right']} | {c.get('unsettled', 0) if fields else '-'} | "
                   f"{c.get('cited_current', 0) if fields else '-'} | {c.get('wrong_value', 0) if fields else '-'} | "
                   f"{c.get('wrong_version', 0) if fields else '-'} | {c.get('wrong_entity', 0) if fields else '-'} | "
                   f"{c.get('leaked', 0)} |")
    trap = [c for t, c in r["by_type"].items() if c.get("version_trap")]
    if trap:
        n, right = sum(c["version_trap"] for c in trap), sum(c["version_trap_right"] for c in trap)
        out += ["", f"Version trap: {n} questions about creditors with an older, superseded authority on file; "
                f"{right} answered from the current one."]
    wrong = [x for x in r["rows"] if not x["right"]]
    if wrong:
        out += ["", "## Wrong answers", "", "| # | Type | Question | Why |", "|---|---|---|---|"]
        out += [f"| {x['id']} | {x['type']} | {x['question']} | {x['why']} |" for x in wrong[:30]]
    out += ["", "## What this does not show", "",
            "- The questions are templated from the generator's truth; real staff ask in many more ways. The intent "
            "routing is rules, and a question it does not recognise falls through to retrieval.",
            "- Answers come from the engine's own readings, so a field the engine misread would be answered wrongly "
            "with a correct citation; the harness measures the readings themselves.", ""]
    return "\n".join(out)


def write(r: dict, path: str | Path, command: str, world_seed: int, size: str) -> None:
    import json
    meta = {"commit": git_stamp(Path.cwd()), "command": command, "seed": world_seed, "size": size}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps({"meta": meta, **r}, indent=2, default=str), encoding="utf-8")
    path.write_text(render(r, meta), encoding="utf-8")


def creditor_names(store: Store, tenant_id: int) -> list[str]:
    with store.tenant(tenant_id) as s:
        return [x.legal_name for x in s.scalars(select(Creditor))]
