"""Command line: generate a world, run the harness, inspect statuses and the evidence behind them."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from sqlalchemy import select

from .model import TIER_ORDER, Verdict
from .pack import load_pack

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="TESTAMUR: evidence assurance for supplier compliance.")


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
    """Generate a synthetic supplier world with documents and ground truth."""
    from .synth.world import build_world
    w = build_world(seed=seed, size=size, out=out)
    typer.echo(f"{len(w.tenants)} tenants, {len(w.suppliers)} suppliers, {len(w.engagements)} engagements, "
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
        where = (f"engagement {i.engagement_id} {i.requirement_id}/{i.check_id}" if i.kind == "check"
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
def review_check(engagement: int, requirement: str, check: str, verdict: str, by: str = BY, note: str = "",
                 kind: str = typer.Option("queue", help="queue (it was waiting for a person) or audit (drawn by "
                                                        "`testamur audit`)"),
                 db: str = "data/harness.db", tenant: int = 1):
    """PASS or FAIL for one check of the document behind a requirement (the reading is right, the judgement
    needs a person)."""
    after = _decide(_engine(db).review_check, tenant, engagement, requirement, check, verdict.upper(), by, note, kind)
    typer.echo(f"{requirement} for engagement {engagement}: {after}")


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
def review_assign(document: int, supplier: int, by: str = BY, note: str = "", db: str = "data/harness.db",
                  tenant: int = 1):
    """Say whose evidence a document is."""
    _decide(_engine(db).assign, tenant, document, supplier, by, note)
    typer.echo(f"document {document}: supplier {supplier}")


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
        typer.echo(f"engagement {i.engagement_id} {i.requirement_id}/{i.check_id} (document {i.document_id}): {i.reason}")
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
              report: str = "reports/review-sim.md", fault: str = "limit-scale",
              audit_rate: float = typer.Option(None, help="one audit rate for every severity (default: the pack's)"),
              seed: int = 1, model_path: str = typer.Option(None), model_url: str = typer.Option(None),
              model_name: str = typer.Option(None), cassette: str = typer.Option(None)):
    """Simulated reviewers who know the truth: work the whole queue, then audit against a planted fault."""
    from .reviewsim import accuracy_scenario, queue_scenario, write
    from .synth.world import load_world
    model = _model(model_path, model_url, model_name, cassette)
    stem = Path(db)
    q = queue_scenario(world_dir, stem.with_name(stem.stem + "_queue.db"), model)
    a = accuracy_scenario(world_dir, stem.with_name(stem.stem + "_fault.db"), model, fault, audit_rate, seed)
    w = load_world(world_dir)
    write(q, a, report, "testamur reviewsim " + " ".join(sys.argv[2:]), w.seed, w.size)
    typer.echo(f"queue: {q['effort']} -> {q['after']}; re-index changed {q['changed_by_reindex']}")
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
def status(db: str = "data/harness.db", tenant: int = 1):
    """Requirement statuses for one tenant."""
    from .store import Engagement, RequirementState, Store, Supplier
    store = Store(f"sqlite:///{db}")
    with store.tenant(tenant) as s:
        names = {x.id: x.legal_name for x in s.scalars(select(Supplier))}
        engs = {e.id: e for e in s.scalars(select(Engagement))}
        for r in sorted(s.scalars(select(RequirementState)), key=lambda r: (r.engagement_id, r.requirement_id)):
            e = engs[r.engagement_id]
            typer.echo(f"{r.engagement_id:4} {names[e.supplier_id][:38]:38} {e.tier:9} {r.requirement_id:24} {r.status}")


@app.command()
def explain(engagement: int, requirement: str, db: str = "data/harness.db", tenant: int = 1):
    """Every check behind one status, with its evidence, the fix hint, and any decision a person made."""
    st, results = _engine(db).verdicts(tenant, engagement, requirement)
    if st is None:
        raise typer.BadParameter("no such engagement and requirement for this tenant")
    typer.echo(f"{requirement} for engagement {engagement}: {st.status} (document {st.document_id})")
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
