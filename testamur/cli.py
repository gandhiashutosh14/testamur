"""Command line: generate a world, run the harness, inspect statuses and the evidence behind them."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from sqlalchemy import select

from .model import TIER_ORDER, Verdict
from .pack import load_pack

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="TESTAMUR: verifying creditors' claims in a corporate insolvency process.")


def _model(model_path: str | None, model_url: str | None, model_name: str | None, cassette: str | None):
    from .modeltier import Cassette, LlamaCppClient, OpenAICompatibleClient
    inner = None
    if model_path:
        inner = LlamaCppClient(model_path)
    elif model_url:
        inner = OpenAICompatibleClient(model_url, model_name or "default")
    if cassette:
        return Cassette(cassette, inner=inner)       # replay-only when no live model is given
    return inner


@app.command()
def world(seed: int = 7, size: str = "small", out: str = "data/world"):
    """Generate a synthetic creditor world with documents and ground truth."""
    from .synth.world import build_world
    w = build_world(seed=seed, size=size, out=out)
    typer.echo(f"{len(w.tenants)} tenants, {len(w.creditors)} creditors, {len(w.claims)} claims, "
               f"{len(w.uploads)} uploads -> {out}")


@app.command()
def harness(world_dir: str = typer.Option("data/world", "--world"), db: str = "data/harness.db",
            report: str = "reports/harness.md", gates: str = "gates.yaml",
            model_path: str = typer.Option(None, help="a local GGUF model for the model tier (llama.cpp)"),
            model_url: str = typer.Option(None, help="an OpenAI-compatible endpoint for the model tier"),
            model_name: str = typer.Option(None),
            cassette: str = typer.Option(None, help="record model calls here; replay-only if no live model"),
            reindex_check: bool = True):
    """Run the engine over a world, measure it against ground truth, apply the gates, write the report."""
    from .harness import apply_gates, run, write
    model = _model(model_path, model_url, model_name, cassette)
    command = "testamur harness " + " ".join(sys.argv[2:])
    r = run(world_dir, db, model, reindex_check=reindex_check, command=command.strip())
    apply_gates(r, gates)
    write(r, report)
    for g in r.gates:
        typer.echo(f"{'ok  ' if g['ok'] else 'FAIL'} {g['gate']}: {g['value']} ({g['op']} {g['limit']})")
    typer.echo(f"report -> {report}")
    raise typer.Exit(0 if r.passed else 1)


@app.command()
def loadtest(world_dir: str = typer.Option("data/world", "--world"), out: str = "data/load",
             report: str = "reports/loadtest.md", workers: str = "1,2,4", budget_pages: int = typer.Option(None),
             crash: bool = True, lease_seconds: float = 20.0):
    """Burst load test: scaling across worker processes, a spend-gate scenario and a crash-recovery scenario."""
    from .loadtest import run, write
    r = run(world_dir, out, [int(x) for x in workers.split(",")], lease_seconds=lease_seconds,
            budget_pages=budget_pages, crash=crash)
    write(r, report, "testamur loadtest " + " ".join(sys.argv[2:]))
    typer.echo(f"report -> {report}")


@app.command()
def shadow(world_dir: str = typer.Option("data/world", "--world"), candidates: str = "fast,en-v4",
           sample: float = 0.4, report: str = "reports/shadow-ocr.md"):
    """Run candidate OCR engines beside the canonical one, observe-only, and apply the parity gate."""
    import json

    from .harness import git_stamp
    from .shadow import render, run
    results = [run(world_dir, c, sample=sample) for c in candidates.split(",")]
    meta = {"commit": git_stamp(Path.cwd()), "command": "testamur shadow " + " ".join(sys.argv[2:])}
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).with_suffix(".json").write_text(json.dumps({"meta": meta, "results": results}, indent=2), encoding="utf-8")
    Path(report).write_text(render(results, meta), encoding="utf-8")
    for r in results:
        typer.echo(f"{r['candidate']}: accuracy {r['canonical_accuracy']:.3f} -> {r['candidate_accuracy']:.3f}, "
                   f"{r['canonical_seconds_per_page']} -> {r['candidate_seconds_per_page']} s/page, "
                   f"cutover {'allowed' if r['cutover_allowed'] else 'refused'}")


def _engine(db: str, model=None):
    """An engine over an existing database, judging at the as-of date the database was evaluated at."""
    from datetime import date

    from .engine import Engine
    from .store import Store
    store = Store(f"sqlite:///{db}")
    as_of = store.setting("as_of")
    blob_root = store.setting("blob_root") or str(Path(db).with_suffix("")) + "_blobs"
    return Engine(store, load_pack(), date.fromisoformat(as_of) if as_of else date.today(), blob_root, model=model)


@app.command()
def queue(db: str = "data/harness.db", tenant: int = 1):
    """What is waiting for a person, most consequential first."""
    items = _engine(db).queue(tenant)
    for i in items:
        where = (f"claim {i.claim_id} {i.requirement_id}/{i.check_id}" if i.kind == "check"
                 else f"document {i.document_id}")
        tail = (f"; candidates {i.options}" if i.options else "") + ("; the engine will retry" if i.retrying else "")
        typer.echo(f"{i.kind:18} {'blocking' if i.blocking else 'finding ':8} {where}: {i.reason}{tail}")
    typer.echo(f"{len(items)} item(s) for tenant {tenant}")


review = typer.Typer(help="Record a person's decision. Each is kept with what the engine had said, audited, and "
                          "applied again on every later run.", no_args_is_help=True)
app.add_typer(review, name="review")
BY = typer.Option(..., "--by", help="who decided")


def _decide(fn, *args):
    try:
        return fn(*args)
    except (KeyError, ValueError) as exc:
        raise typer.BadParameter(str(exc).strip("'\"")) from exc


@review.command("check")
def review_check(claim: int, requirement: str, check: str, verdict: str, by: str = BY, note: str = "",
                 kind: str = typer.Option("queue", help="queue (it was waiting for a person) or audit (drawn by "
                                                        "`testamur audit`)"),
                 db: str = "data/harness.db", tenant: int = 1):
    """PASS or FAIL for one check of the document behind a requirement (the reading is right, the judgement
    needs a person)."""
    after = _decide(_engine(db).review_check, tenant, claim, requirement, check, verdict.upper(), by, note, kind)
    typer.echo(f"{requirement} for claim {claim}: {after}")


@review.command("recertify")
def review_recertify(check: str, by: str = BY, note: str = "", db: str = "data/harness.db", tenant: int = 1):
    """A person found why a check breached its budget and dealt with it: measure it afresh from here."""
    _decide(_engine(db).recertify, tenant, check, by, note)
    typer.echo(f"{check}: re-certified; its accuracy is measured afresh")


@review.command("field")
def review_field(document: int, field: str, value: str, by: str = BY, note: str = "", db: str = "data/harness.db",
                 tenant: int = 1):
    """Replace what extraction read for one field; the rules then judge the person's reading."""
    _decide(_engine(db).correct_field, tenant, document, field, value, by, note)
    typer.echo(f"document {document}: {field} = {value!r}")


@review.command("classify")
def review_classify(document: int, doc_type: str, by: str = BY, note: str = "", db: str = "data/harness.db",
                    tenant: int = 1):
    """Say what a document is."""
    _decide(_engine(db).classify_document, tenant, document, doc_type, by, note)
    typer.echo(f"document {document}: {doc_type}")


@review.command("assign")
def review_assign(document: int, creditor: int, by: str = BY, note: str = "", db: str = "data/harness.db",
                  tenant: int = 1):
    """Say whose evidence a document is."""
    _decide(_engine(db).assign, tenant, document, creditor, by, note)
    typer.echo(f"document {document}: creditor {creditor}")


@review.command("reject")
def review_reject(document: int, by: str = BY, note: str = "", db: str = "data/harness.db", tenant: int = 1):
    """Say a document is nobody's evidence."""
    _decide(_engine(db).reject, tenant, document, by, note)
    typer.echo(f"document {document}: rejected")


@review.command("clear")
def review_clear(document: int, by: str = BY, note: str = "", db: str = "data/harness.db", tenant: int = 1):
    """A person read a document that carries text aimed at automated readers and accepts it as evidence."""
    _decide(_engine(db).clear, tenant, document, by, note)
    typer.echo(f"document {document}: cleared")


@app.command()
def audit(db: str = "data/harness.db", tenant: int = 1, seed: str = "audit"):
    """Draw the audit sample: a random share of the engine's conclusive verdicts for people to check."""
    items = _engine(db).audit_sample(tenant, seed)
    for i in items:
        typer.echo(f"claim {i.claim_id} {i.requirement_id}/{i.check_id} (document {i.document_id}): {i.reason}")
    typer.echo(f"{len(items)} verdict(s) to audit; record each with: testamur review check ... --kind audit")


@app.command()
def health(db: str = "data/harness.db", tenant: int = 1):
    """Per-check accuracy from audits, and whether each check's PASS still vouches."""
    rows = _engine(db).health(tenant)
    typer.echo(f"{'check':28} {'severity':9} {'budget':>6} {'audited PASS':>12} {'overturned':>10} {'upper 95%':>9}  state")
    for h in rows.values():
        upper = f"{h.upper:.1%}" if h.upper is not None else "-"
        typer.echo(f"{h.check_id:28} {h.severity:9} {h.budget:>6.0%} {h.audited_pass:>12} {h.false_pass:>10} "
                   f"{upper:>9}  {h.state}" + (f" ({h.incidents} incident(s))" if h.incidents else ""))


@app.command()
def reviewsim(world_dir: str = typer.Option("data/world", "--world"), db: str = "data/reviewsim.db",
              report: str = "reports/review-sim.md", fault: str = "limitation-years",
              audit_rate: float = typer.Option(None, help="one audit rate for every severity (default: the pack's)"),
              seed: int = 1, scenarios: str = typer.Option("queue,fault", help="queue, fault, or both"),
              model_path: str = typer.Option(None), model_url: str = typer.Option(None),
              model_name: str = typer.Option(None), cassette: str = typer.Option(None)):
    """Simulated reviewers who know the truth: work the whole queue, then audit against a planted fault."""
    from .reviewsim import accuracy_scenario, queue_scenario, write
    from .synth.world import load_world
    model = _model(model_path, model_url, model_name, cassette)
    stem, wanted = Path(db), set(scenarios.split(","))
    q = queue_scenario(world_dir, stem.with_name(stem.stem + "_queue.db"), model) if "queue" in wanted else None
    a = (accuracy_scenario(world_dir, stem.with_name(stem.stem + "_fault.db"), model, fault, audit_rate, seed)
         if "fault" in wanted else None)
    w = load_world(world_dir)
    write(q, a, report, "testamur reviewsim " + " ".join(sys.argv[2:]), w.seed, w.size)
    if q:
        typer.echo(f"queue: {q['effort']} -> {q['after']}; re-index changed {q['changed_by_reindex']}")
    if a:
        typer.echo(f"fault: before {a['before']}, after audits {a['after_audit']}, final {a['final']}")
    typer.echo(f"report -> {report}")


@app.command()
def calibrate(seeds: str = "21-30", size: str = "full", alpha: str = "0.01,0.02,0.05", delta: float = 0.05,
              out: str = "data/calibration", report: str = "reports/calibration-matching.md",
              validate_world: str = typer.Option(None, help="a world with ground truth to check the threshold on"),
              validate_db: str = typer.Option(None, help="that world's processed database (from testamur harness)")):
    """Choose the name-matching threshold by Learn then Test on worlds with ground truth; check it on another."""
    import json

    from .calibrate import evaluate, labelled_names, learn_then_test, render
    from .harness import git_stamp, process_world
    from .match import ACCEPT
    from .store import Store
    from .synth.world import build_world, load_world
    first, last = (int(x) for x in seeds.split("-")) if "-" in seeds else (int(seeds), int(seeds))
    rows = []
    for seed in range(first, last + 1):
        wdir = Path(out) / f"world_{seed}"
        build_world(seed=seed, size=size, out=wdir, digital_only=True)
        world, store, _, _ = process_world(wdir, Path(out) / f"cal_{seed}.db", None)
        rows += labelled_names(world, store)
        store.engine.dispose()
        typer.echo(f"seed {seed}: {len(rows)} labelled documents so far")
    cal = [learn_then_test(rows, float(a), delta) for a in alpha.split(",")]
    validation = None
    if validate_world and validate_db:
        vrows = labelled_names(load_world(validate_world), Store(f"sqlite:///{validate_db}"))
        thresholds = sorted({c["threshold"] for c in cal if c["threshold"] is not None} | {ACCEPT}, reverse=True)
        validation = {"label": Path(validate_world).name, "rows": len(vrows),
                      "results": [evaluate(vrows, th) for th in thresholds]}
    meta = {"commit": git_stamp(Path.cwd()), "command": "testamur calibrate " + " ".join(sys.argv[2:])}
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).with_suffix(".json").write_text(json.dumps({"meta": meta, "calibration": cal,
                                                             "validation": validation}, indent=2), encoding="utf-8")
    Path(report).write_text(render(cal, len(rows), seeds, validation, meta), encoding="utf-8")
    for c in cal:
        typer.echo(f"alpha {c['alpha']}: certified threshold {c['threshold']} (n={c['n']})")
    typer.echo(f"report -> {report}")


@app.command()
def dispatch(db: str = "data/harness.db", tenant: int = 1):
    """Turn reported status changes into work: tasks, creditor reminders, holds and webhook messages."""
    from .actions import dispatch as run_dispatch
    from .store import utcnow
    eng = _engine(db)
    done = run_dispatch(eng.store, eng.pack, tenant, utcnow())
    typer.echo(", ".join(f"{k} {v}" for k, v in sorted(done.items())) or "nothing to dispatch")


@app.command()
def tasks(db: str = "data/harness.db", tenant: int = 1, show_closed: bool = False):
    """Open remediation work (and closed, with --show-closed), and the claims on hold (votes not counted, nothing paid)."""
    from .store import Hold, Task
    eng = _engine(db)
    with eng.store.tenant(tenant) as s:
        q = select(Task).order_by(Task.due_at) if show_closed else select(Task).where(Task.status == "open").order_by(
            Task.due_at)
        for t in s.scalars(q):
            typer.echo(f"{t.status:10} due {t.due_at:%Y-%m-%d} {t.kind:15} {t.title}")
        for h in s.scalars(select(Hold).where(Hold.released_at.is_(None))):
            typer.echo(f"HOLD       claim {h.claim_id} since {h.placed_at:%Y-%m-%d}: {', '.join(h.requirements)}")


@app.command()
def actionsim(world_dir: str = typer.Option("data/world", "--world"), db: str = "data/actionsim.db",
              report: str = "reports/action-sim.md", days: int = 60, response_rate: float = 0.15, seed: int = 1,
              outage: float = 0.2, ack_loss: float = 0.1, cassette: str = typer.Option(None)):
    """Simulate days passing: findings become work, creditors answer, webhooks are retried; the loop is measured."""
    from .actionsim import run, write
    from .synth.world import load_world
    r = run(world_dir, db, days, response_rate, seed, outage, ack_loss, _model(None, None, None, cassette))
    w = load_world(world_dir)
    write(r, report, "testamur actionsim " + " ".join(sys.argv[2:]), w.seed, w.size)
    typer.echo(f"tasks {r['tasks']}; holds placed {r['totals'].get('hold_placed', 0)}, released "
               f"{r['totals'].get('hold_released', 0)}; deliveries {r['deliveries']}; invariants {r['invariants']}")
    typer.echo(f"report -> {report}")


@app.command()
def ask(question: str, db: str = "data/harness.db", tenant: int = 1):
    """Ask the assistant about creditors and their evidence; every answer cites the page it read."""
    from .assistant import Assistant
    a = Assistant(_engine(db)).ask(tenant, question)
    typer.echo(a.text)
    for c in a.citations:
        typer.echo(f"  [document {c.document_id}, page {c.page}] {c.quote}")
    for w in a.warnings:
        typer.echo(f"  ! {w}")


@app.command()
def golden(world_dir: str = typer.Option("data/world", "--world"), db: str = "data/harness.db",
           report: str = "reports/assistant-golden.md"):
    """Score the assistant on a golden question set built from the world's truth, traps included."""
    from .golden import build, evaluate, write
    from .synth.world import load_world
    world = load_world(world_dir)
    eng = _engine(db)
    r = evaluate(world, eng.store, eng, build(world))
    write(r, report, "testamur golden " + " ".join(sys.argv[2:]), world.seed, world.size)
    typer.echo(f"{r['right']} of {r['questions']} answered right; report -> {report}")


keys = typer.Typer(help="Keys for agents: each acts for one tenant.", no_args_is_help=True)
tools_app = typer.Typer(help="The tenant's tool catalogue: what its agents may call.", no_args_is_help=True)
proposals = typer.Typer(help="Changes agents proposed: a person confirms or declines each.", no_args_is_help=True)
app.add_typer(keys, name="keys")
app.add_typer(tools_app, name="tools")
app.add_typer(proposals, name="proposals")


@keys.command("create")
def keys_create(name: str, db: str = "data/harness.db", tenant: int = 1):
    """Create a key for an agent; it is shown once."""
    from .agents import create_key
    typer.echo(create_key(_engine(db).store, tenant, name))


@tools_app.command("grant")
def tools_grant(tool: str, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    from .agents import grant
    _decide(grant, _engine(db).store, tenant, tool, by)
    typer.echo(f"{tool}: granted")


@tools_app.command("revoke")
def tools_revoke(tool: str, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    from .agents import revoke
    revoke(_engine(db).store, tenant, tool, by)
    typer.echo(f"{tool}: revoked")


@tools_app.command("list")
def tools_list(db: str = "data/harness.db", tenant: int = 1):
    from .agents import TOOLS, granted
    have = granted(_engine(db).store, tenant)
    for tool in TOOLS:
        typer.echo(f"{'granted' if tool in have else '-':8} {tool}")


@proposals.command("list")
def proposals_list(db: str = "data/harness.db", tenant: int = 1):
    from .agents import pending_proposals
    for p in pending_proposals(_engine(db).store, tenant):
        typer.echo(f"{p['proposal']:5} by {p['by']}: {p['summary']} (expires {p['expires']})")


@proposals.command("confirm")
def proposals_confirm(proposal: int, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    """Apply an agent's proposal, under your name with the agent's beside it."""
    from .agents import decide
    typer.echo(_decide(decide, _engine(db), tenant, proposal, by, True))


@proposals.command("decline")
def proposals_decline(proposal: int, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    from .agents import decide
    typer.echo(_decide(decide, _engine(db), tenant, proposal, by, False))


@app.command()
def mcp(db: str = "data/harness.db"):
    """Serve the tenant-scoped tools over MCP (stdio). The agent's key comes from TESTAMUR_API_KEY."""
    import asyncio
    import os

    from .mcp_server import build_server
    server = build_server(_engine(db), os.environ.get("TESTAMUR_API_KEY", ""))
    asyncio.run(server.run_stdio_async())


@app.command()
def status(db: str = "data/harness.db", tenant: int = 1):
    """Requirement statuses for one tenant."""
    from .store import Claim, Creditor, RequirementState, Store
    store = Store(f"sqlite:///{db}")
    with store.tenant(tenant) as s:
        names = {x.id: x.legal_name for x in s.scalars(select(Creditor))}
        engs = {e.id: e for e in s.scalars(select(Claim))}
        for r in sorted(s.scalars(select(RequirementState)), key=lambda r: (r.claim_id, r.requirement_id)):
            e = engs[r.claim_id]
            typer.echo(f"{r.claim_id:4} {names[e.creditor_id][:38]:38} {e.tier:9} {r.requirement_id:24} {r.status}")


@app.command()
def explain(claim: int, requirement: str, db: str = "data/harness.db", tenant: int = 1):
    """Every check behind one status, with its evidence, the fix hint, and any decision a person made."""
    st, results = _engine(db).verdicts(tenant, claim, requirement)
    if st is None:
        raise typer.BadParameter("no such claim and requirement for this tenant")
    typer.echo(f"{requirement} for claim {claim}: {st.status} (document {st.document_id})")
    for r in sorted(results, key=lambda r: TIER_ORDER[r.tier]):          # stable: pack order within a tier
        typer.echo(f"  [{r.tier:8}] {r.check_id:28} {r.verdict:7} {r.reason}")
        for ev in r.evidence:
            if ev.source == "reviewer" and not ev.quote:
                continue
            where = "person" if ev.source == "reviewer" else (f"p.{ev.page}" if ev.page is not None else "record")
            typer.echo(f"             evidence {where}: {ev.quote}")
        if r.verdict == Verdict.FAIL and r.fix_hint:
            typer.echo(f"             fix: {r.fix_hint}")


if __name__ == "__main__":
    app()
    Path  # noqa: B018
