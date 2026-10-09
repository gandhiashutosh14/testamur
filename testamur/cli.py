"""Command line: generate a world, run the harness, inspect statuses and the evidence behind them."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from sqlalchemy import select

from .model import Verdict

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
    """Every check behind one status, with its evidence and the fix hint."""
    from .store import RequirementState, Store, VerdictRow
    store = Store(f"sqlite:///{db}")
    with store.tenant(tenant) as s:
        st = s.get(RequirementState, {"engagement_id": engagement, "requirement_id": requirement, "tenant_id": tenant})
        if st is None:
            raise typer.BadParameter("no such engagement and requirement for this tenant")
        typer.echo(f"{requirement} for engagement {engagement}: {st.status} (document {st.document_id})")
        rows = s.scalars(select(VerdictRow).where(VerdictRow.engagement_id == engagement,
                                                  VerdictRow.requirement_id == requirement,
                                                  VerdictRow.document_id == st.document_id)).all()
        for v in rows:
            typer.echo(f"  [{v.tier:8}] {v.check_id:28} {v.verdict:7} {v.reason}")
            for ev in v.evidence or []:
                typer.echo(f"             evidence p.{ev.get('page')}: {ev.get('quote')}")
            if v.verdict == Verdict.FAIL and v.fix_hint:
                typer.echo(f"             fix: {v.fix_hint}")


if __name__ == "__main__":
    app()
    Path  # noqa: B018
