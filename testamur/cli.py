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


def _model(model_path: str | None, model_url: str | None, model_name: str | None, cassette: str | None,
           ctx: int = 4096):
    from .modeltier import Cassette, LlamaCppClient, OpenAICompatibleClient
    inner = None
    if model_path:
        inner = LlamaCppClient(model_path, n_ctx=ctx)
    elif model_url:
        inner = OpenAICompatibleClient(model_url, model_name or "default")
    if cassette:
        return Cassette(cassette, inner=inner)       # replay-only when no live model is given
    return inner


@app.command()
def world(seed: int = 7, size: str = "small", out: str = "data/world"):
    """Generate a synthetic world of insolvency processes, with documents and ground truth."""
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
            typer.echo(f"HOLD {h.action:6} claim {h.claim_id} since {h.placed_at:%Y-%m-%d}: {'; '.join(h.reasons)}")


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
def keys_create(name: str, db: str = "data/harness.db", tenant: int = 1,
                role: str = typer.Option("agent", help="agent (the agent surface), uploader or reviewer (the service)")):
    """Create a key; it is shown once."""
    from .agents import create_key
    typer.echo(create_key(_engine(db).store, tenant, name, role))


@keys.command("revoke")
def keys_revoke(name: str, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    """Revoke an agent's keys; servers already running refuse its next call."""
    from .agents import revoke_key
    typer.echo(f"{revoke_key(_engine(db).store, tenant, name, by)} key(s) revoked")


AGENT = typer.Option("*", "--agent", help="the agent the grant is for; * for every agent of the tenant")


@tools_app.command("grant")
def tools_grant(tool: str, by: str = BY, agent: str = AGENT, db: str = "data/harness.db", tenant: int = 1):
    from .agents import grant
    _decide(grant, _engine(db).store, tenant, tool, by, agent)
    typer.echo(f"{tool}: granted to {agent}")


@tools_app.command("revoke")
def tools_revoke(tool: str, by: str = BY, agent: str = AGENT, db: str = "data/harness.db", tenant: int = 1):
    from .agents import revoke
    revoke(_engine(db).store, tenant, tool, by, agent)
    typer.echo(f"{tool}: revoked from {agent}")


@tools_app.command("list")
def tools_list(agent: str = typer.Option(None, "--agent", help="what one agent may call"), db: str = "data/harness.db",
               tenant: int = 1):
    from .agents import TOOLS, granted
    have = granted(_engine(db).store, tenant, agent)
    for tool in TOOLS:
        typer.echo(f"{'granted' if tool in have else '-':8} {tool}")


@proposals.command("list")
def proposals_list(db: str = "data/harness.db", tenant: int = 1):
    from .agents import pending_proposals
    for p in pending_proposals(_engine(db).store, tenant):
        typer.echo(f"{p['proposal']:5} by {p['by']}: {p['summary']} (digest {p['digest']}, expires {p['expires']})")


@proposals.command("confirm")
def proposals_confirm(proposal: int, by: str = BY,
                      digest: str = typer.Option(None, help="the digest you were shown; refused if it differs"),
                      db: str = "data/harness.db", tenant: int = 1):
    """Apply an agent's proposal, under your name with the agent's beside it, if its evidence is still what it was
    made on (otherwise it is marked stale and nothing changes)."""
    from .agents import decide
    typer.echo(_decide(decide, _engine(db), tenant, proposal, by, True, None, digest))


@proposals.command("decline")
def proposals_decline(proposal: int, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    from .agents import decide
    typer.echo(_decide(decide, _engine(db), tenant, proposal, by, False))


investigate_app = typer.Typer(help="The investigator-verifier workflow on one claim: two agents read, a coordinator "
                                    "validates and proposes, a person approves.", no_args_is_help=True)
app.add_typer(investigate_app, name="investigate")


AGENT_CTX = 16384      # an agent's conversation: its task, the tools, and several pages read


def _flow(db: str, model=None):
    from .investigate import Investigations, sqlite_checkpointer
    saver, _ = sqlite_checkpointer(str(Path(db).with_suffix("")) + ".checkpoints.sqlite")
    return Investigations(_engine(db), model, saver)


def _show_run(run: dict) -> None:
    import json
    typer.echo(f"run {run['run']} on claim {run['claim']} ({run['mode']}): {run['status']}, {run['tokens']} tokens")
    for e in run["trace"]:
        if e["kind"] == "agent":
            typer.echo(f"  {e['role']:12} stopped: {e['outcome']} after {e['detail'].get('steps')} steps")
        else:
            typer.echo(f"  {e['role']:12} step {e['step']}: {e['tool']}({json.dumps(e['args'])}) {e['outcome']}"
                       + (f": {e['reason']}" if e["reason"] else ""))
    for r in run["recommendations"]:
        typer.echo(f"  -> {r['action']:16} {r['requirement']}/{r['check'] or '-'} {r.get('verdict') or ''} "
                   f"[{r['agreement']}] {r['reason'][:120]}"
                   + (f" (proposal {r['proposal_id']}, digest {r['digest']})" if r.get("proposal_id") else ""))


@investigate_app.command("start")
def investigate_start(claim: int, by: str = BY, mode: str = typer.Option("pair", help="pair or single"),
                      model_path: str = typer.Option(None, help="a local GGUF model (llama.cpp)"),
                      cassette: str = typer.Option(None, help="record calls here; replay-only without a model"),
                      db: str = "data/harness.db", tenant: int = 1):
    """Investigate one claim: the investigator builds a cited evidence packet, the verifier checks it, the coordinator
    proposes what only a person may apply."""
    _show_run(_decide(_flow(db, _model(model_path, None, None, cassette, AGENT_CTX)).start, tenant, claim, by, mode))


@investigate_app.command("show")
def investigate_show(run: int, db: str = "data/harness.db", tenant: int = 1):
    _show_run(_decide(_flow(db).status, tenant, run))


@investigate_app.command("approve")
def investigate_approve(run: int, proposal: int, by: str = BY, digest: str = typer.Option(..., help="as shown"),
                        decline: bool = False, db: str = "data/harness.db", tenant: int = 1):
    """Approve (or --decline) one of a run's proposals, as shown with its digest; it is applied only if its evidence
    is unchanged."""
    _show_run(_decide(_flow(db).resume, tenant, run, [{"proposal": proposal, "approve": not decline,
                                                       "digest": digest}], by))


@investigate_app.command("recover")
def investigate_recover(run: int, model_path: str = typer.Option(None), cassette: str = typer.Option(None),
                        db: str = "data/harness.db", tenant: int = 1):
    """Continue a run whose process died, from its last finished step."""
    _show_run(_decide(_flow(db, _model(model_path, None, None, cassette, AGENT_CTX)).recover, tenant, run))


@investigate_app.command("cancel")
def investigate_cancel(run: int, by: str = BY, db: str = "data/harness.db", tenant: int = 1):
    _show_run(_decide(_flow(db).cancel, tenant, run, by))


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


db_app = typer.Typer(help="The schema, by migration (row-level security included on Postgres).", no_args_is_help=True)
demo_app = typer.Typer(help="The demo stack's synthetic data and keys.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(demo_app, name="demo")


def _db_url(url: str | None) -> str:
    import os
    return url or os.environ.get("TESTAMUR_DATABASE_URL") or "sqlite:///data/service.db"


@db_app.command("upgrade")
def db_upgrade(url: str = typer.Option(None, help="the owner's URL; TESTAMUR_DATABASE_URL by default"),
               app_role: str = typer.Option(None, help="the role row-level security admits; TESTAMUR_APP_ROLE or "
                                                       "testamur_app by default")):
    """Bring the database to the current schema, and the workflow's checkpoint tables when they live in Postgres."""
    import os

    from . import migrations
    target = _db_url(url)
    migrations.upgrade(target, app_role=app_role)
    typer.echo(f"schema at {migrations.current(target)} (head {migrations.head()})")
    where = os.environ.get("TESTAMUR_CHECKPOINTS", "")
    if where.startswith(("postgresql://", "postgresql+psycopg://", "postgres://")):
        from .investigate import checkpointer
        _, pool = checkpointer(where)          # creates LangGraph's own tables, idempotently
        pool.close()
        typer.echo("checkpoint tables ready")


@db_app.command("current")
def db_current(url: str = typer.Option(None, help="TESTAMUR_DATABASE_URL by default")):
    from . import migrations
    typer.echo(f"schema at {migrations.current(_db_url(url))} (head {migrations.head()})")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """The service (API and review page), configured by TESTAMUR_* variables (testamur/service/settings.py)."""
    import uvicorn
    uvicorn.run("testamur.service:create_app", factory=True, host=host, port=port, proxy_headers=True,
                log_level="info")


@app.command("worker")
def worker_cmd(name: str = "worker-1", lease_seconds: float = 120.0,
               serve_forever: bool = typer.Option(True, "--serve/--drain", help="keep polling, or exit when idle")):
    """A worker for the service: reads uploads and runs investigations from the shared queue."""
    import json
    import signal
    import threading

    from .service.runtime import Runtime
    from .service.settings import Settings
    from .telemetry import configure
    from .worker import work
    configure("testamur-worker")
    rt = Runtime(Settings.from_env(require_secret=False))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())        # finish the job in hand, then exit
    try:
        stats = work(rt.engine, name, investigations=rt.investigations, lease_seconds=lease_seconds,
                     forever=serve_forever, stop=stop)
    finally:
        rt.close()
    typer.echo(json.dumps(stats))


@demo_app.command("seed")
def demo_seed(out: str = "data/demo-world", world_seed: int = 3):
    """Load the synthetic world through the service's intake and print fresh demo keys (shown once)."""
    from .service.demo import seed
    from .service.runtime import Runtime
    from .service.settings import Settings
    rt = Runtime(Settings.from_env(require_secret=False))
    try:
        result = seed(rt, out, world_seed)
    finally:
        rt.close()
    typer.echo(f"registry {result['registry']}: {result['tenants']} firms, {result['claims']} claims; "
               f"{result['uploads_queued']} upload(s) queued for the worker")
    for name, k in result["keys"].items():
        typer.echo(f"  {k['role']:8} key for firm {k['tenant']} ({name}): {k['key']}")
    typer.echo("Sign in at http://localhost:8000/login with a reviewer key. Synthetic data only.")


eval_app = typer.Typer(help="The holdout comparison: the rules alone, one agent, and the investigator-verifier "
                            "pair, on cases written by hand.", no_args_is_help=True)
app.add_typer(eval_app, name="evaluate")


@eval_app.command("run")
def evaluate_run(cases: str = "evals/holdout/cases.yaml", out: str = "artifacts/holdout-0.json",
                 work: str = "data/holdout", shard: str = typer.Option("0/1", help="this shard of n: i/n"),
                 arms: str = "rules,single,pair",
                 model_path: str = typer.Option(None, help="the agents' GGUF model (llama.cpp, CPU)"),
                 ctx: int = typer.Option(16384, help="the agents' context window, in tokens"),
                 threads: int = typer.Option(None, help="CPU threads for the model"),
                 cassette: str = typer.Option(None, help="record calls here; replay-only without a model")):
    """Run the arms over one shard of the cases and write its results."""
    import json

    from . import evaluate as ev
    from .modeltier import Cassette, LlamaCppClient
    inner = LlamaCppClient(model_path, n_ctx=ctx, threads=threads) if model_path else None
    model = Cassette(cassette, inner=inner) if cassette else inner
    i, n = (int(x) for x in shard.split("/"))
    result = ev.evaluate(ev.load(cases), Path(work) / f"shard-{i}", model, arms=tuple(arms.split(",")), shard=(i, n))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    typer.echo(f"{len(result['cases'])} case(s) -> {out}")


@eval_app.command("report")
def evaluate_report(results: list[str], cases: str = "evals/holdout/cases.yaml",
                    report: str = "reports/holdout-eval.md", data: str = "reports/holdout-eval.json",
                    work: str = "data/holdout-faults",
                    earlier: list[str] | None = typer.Option(None, help="an earlier run's report data, oldest first"),
                    change: list[str] | None = typer.Option(None, help="what changed after each earlier run, in order"),
                    timings_from: str = typer.Option(None, help="for a replay, which measures no timings: the report "
                                                                "data of the run whose recording it replayed")):
    """Merge the shards' results, force the fault scenarios, and write the report and its data."""
    import json
    import re
    from datetime import UTC, datetime

    from . import evaluate as ev
    from .harness import git_stamp
    hs = ev.load(cases)
    merged = ev.merge([json.loads(Path(p).read_text(encoding="utf-8")) for p in results])
    if merged["set"] != hs.digest:
        raise typer.BadParameter(f"the results are about case set {merged['set']}, the file is {hs.digest}")
    if timings_from:
        merged = ev.with_timings(merged, json.loads(Path(timings_from).read_text(encoding="utf-8")))
    before, change = [], change or []
    for i, path in enumerate(earlier or []):
        prior = json.loads(Path(path).read_text(encoding="utf-8"))
        if prior["set"] != hs.digest:
            raise typer.BadParameter(f"{path} is about case set {prior['set']}, not {hs.digest}")
        stamp = re.search(r"at commit `([^`]+)`", Path(path).with_suffix(".md").read_text(encoding="utf-8"))
        before.append({"label": f"run {i + 1}", "commit": stamp.group(1) if stamp else "?",
                       "summary": prior["summary"], "change": change[i] if i < len(change) else ""})
    rows = ev.faults(hs, work)
    meta = {"generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_stamp(Path.cwd()),
            "command": "testamur evaluate report " + " ".join(Path(p).name for p in results)
            + "".join(f" --earlier {Path(p).name}" for p in earlier or [])
            + (f" --timings-from {Path(timings_from).name}" if timings_from else "")}
    Path(report).write_text(ev.render(merged, meta, rows, before), encoding="utf-8", newline="\n")
    Path(data).write_text(json.dumps({**merged, "summary": ev.summarise(merged), "faults": rows,
                                      "earlier": before, "meta": meta}, indent=1), encoding="utf-8", newline="\n")
    typer.echo(f"{report}: {len(merged['cases'])} cases, {sum(f['held'] for f in rows)} of {len(rows)} faults held")


@eval_app.command("check")
def evaluate_check(results: list[str], against: str = "reports/holdout-eval.json"):
    """Fail unless these results give every case what the committed report gives it (a replay's check): every arm's
    output and every run's path (status, steps, tool calls, citations, recommendations, tokens), timings aside, and no
    case twice."""
    import json

    from . import evaluate as ev
    try:
        merged = ev.merge([json.loads(Path(p).read_text(encoding="utf-8")) for p in results])
    except ValueError as exc:
        typer.echo(f"the results do not merge: {exc}")
        raise typer.Exit(1) from None
    committed = json.loads(Path(against).read_text(encoding="utf-8"))
    differ = ev.differences(merged["cases"], committed["cases"])
    if merged["set"] != committed["set"] or differ:
        typer.echo(f"the cases differ from {against}: set {merged['set']} vs {committed['set']}; cases {differ}")
        raise typer.Exit(1)
    typer.echo(f"{len(merged['cases'])} cases: every one as committed")


corpus_app = typer.Typer(help="The public datasets the evidence QA is measured on: fetched, verified, converted.",
                         no_args_is_help=True)
evidence_app = typer.Typer(help="Evidence QA across four domains, scored against ground truth and by RAGAS.",
                           no_args_is_help=True)
app.add_typer(corpus_app, name="corpus")
app.add_typer(evidence_app, name="evidence")


@corpus_app.command("fetch")
def corpus_fetch(root: str = "data/corpus", only: str = typer.Option("", help="comma-separated: cuad,tatqa,...")):
    """Download what is missing (about 100 MB in all), verify each file against its pinned checksum, and convert."""
    from .evidence.sample import build
    from .evidence.sources import fetch
    for src, path, downloaded in fetch(root, [x for x in only.split(",") if x] or None):
        typer.echo(f"{'downloaded' if downloaded else 'verified  '} {src.name:14} {src.licence:9} {path}")
    for name, st in build(root).items():
        typer.echo(f"  {name:12} {st['documents']:4} documents, {st['passages']:6} passages, {st['tasks']:5} tasks "
                   f"({st['answerable']} answerable)")


@corpus_app.command("sample")
def corpus_sample(root: str = "data/corpus", out: str = "evals/evidence/manifest.json"):
    """Draw the stratified, seeded sample the evaluation runs on (commit it before any model runs)."""
    import json

    from .evidence.sample import manifest
    m = manifest(root)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(m, indent=1) + "\n", encoding="utf-8", newline="\n")
    typer.echo(f"{sum(len(d['tasks']) for d in m['datasets'].values())} tasks -> {out}")


def _cpu_model(path: str | None, cassette: str | None, ctx: int, threads: int | None):
    from .modeltier import Cassette, LlamaCppClient
    inner = LlamaCppClient(path, n_ctx=ctx, threads=threads) if path else None
    if cassette:
        return Cassette(cassette, inner=inner)
    if inner is None:
        raise typer.BadParameter("a model path, a recording to replay, or both")
    return inner


@evidence_app.command("run")
def evidence_run(manifest: str = "evals/evidence/manifest.json", corpus: str = "data/corpus",
                 out: str = "artifacts/evidence-0.json", shard: str = typer.Option("0/1", help="this shard of n: i/n"),
                 limit: int = typer.Option(None, help="the first n tasks of the shard only"),
                 model_path: str = typer.Option(None, help="the answering model's GGUF file"),
                 cassette: str = typer.Option(None, help="record the answers here; replay-only without a model"),
                 judge_path: str = typer.Option(None, help="the RAGAS judge's GGUF file (another model family)"),
                 judge_cassette: str = typer.Option(None, help="record the judge's calls here"),
                 no_ragas: bool = typer.Option(False, "--no-ragas", help="ground-truth metrics only"),
                 embedder: str = typer.Option("fastembed", help="fastembed (bge-small) or hash (no download)"),
                 threads: int = typer.Option(None), ctx: int = 8192):
    """Run one shard of the sample: retrieval, answers, ground truth and RAGAS; write its results."""
    import json

    from .evidence.retrieve import FastEmbedder, HashEmbedder
    from .evidence.run import evaluate
    emb = FastEmbedder() if embedder == "fastembed" else HashEmbedder()
    generator = _cpu_model(model_path, cassette, ctx, threads)
    judge = None
    if not no_ragas:
        from .evidence.judge import Judge
        judge = Judge(_cpu_model(judge_path, judge_cassette, ctx, threads), emb)
    i, n = (int(x) for x in shard.split("/"))
    result = evaluate(manifest, corpus, generator, judge, emb, shard=(i, n), limit=limit)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    typer.echo(f"{len(result['tasks'])} task(s) -> {out}")


@evidence_app.command("report")
def evidence_report(results: list[str], report: str = "reports/evidence-eval.md",
                    data: str = "reports/evidence-eval.json",
                    timings_from: str = typer.Option(None, help="for a replay, which measures no timings: the report "
                                                                "data of the run whose recording it replayed"),
                    manifest: str = "evals/evidence/manifest.json"):
    """Merge the shards' results and write the report and its data."""
    import json
    from datetime import UTC, datetime

    from .evidence.run import by_dataset, in_sample, merge, render, with_timings
    from .harness import git_stamp
    merged = merge([json.loads(Path(p).read_text(encoding="utf-8")) for p in results])
    in_sample(merged, manifest)
    if timings_from:
        merged = with_timings(merged, json.loads(Path(timings_from).read_text(encoding="utf-8")))
    meta = {"generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_stamp(Path.cwd()),
            "command": "testamur evidence report " + " ".join(Path(p).name for p in results)
            + (f" --timings-from {Path(timings_from).name}" if timings_from else "")}
    Path(report).write_text(render(merged, meta), encoding="utf-8", newline="\n")
    Path(data).write_text(json.dumps({**merged, "summary": by_dataset(merged), "meta": meta}, indent=1),
                          encoding="utf-8", newline="\n")
    typer.echo(f"{report}: {len(merged['tasks'])} tasks")


@evidence_app.command("check")
def evidence_check(results: list[str], against: str = "reports/evidence-eval.json",
                   manifest: str = "evals/evidence/manifest.json"):
    """Fail unless these results give every task what the committed report gives it (a replay's check): retrieval,
    answer, citations, verdicts, tokens and judgements exactly, answer relevancy (embeddings computed where it replays)
    within 0.001, and no task twice."""
    import json

    from .evidence.run import differences, in_sample, merge
    try:
        merged = merge([json.loads(Path(p).read_text(encoding="utf-8")) for p in results])
        in_sample(merged, manifest, every=True)
    except ValueError as exc:
        typer.echo(f"the results do not hold: {exc}")
        raise typer.Exit(1) from None
    now = merged["tasks"]
    differ = differences(now, json.loads(Path(against).read_text(encoding="utf-8"))["tasks"])
    if differ:
        typer.echo(f"{len(differ)} task(s) differ from {against}: {differ[:10]}")
        raise typer.Exit(1)
    typer.echo(f"{len(now)} tasks: every one as committed")


if __name__ == "__main__":
    app()
    Path  # noqa: B018
