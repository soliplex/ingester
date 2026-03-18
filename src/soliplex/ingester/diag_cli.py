import asyncio
import json
import logging
import platform

import typer
from pydantic_core import ValidationError
from rich import print
from rich.syntax import Syntax
from rich.table import Table

from .lib.config import get_settings

logger = logging.getLogger(__name__)


def init():
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        settings = get_settings()
        logging.basicConfig(
            level=settings.log_level,
            format=settings.log_format,
            datefmt="%Y-%m-%dT%H:%M:%S",
            style="{",
        )
    except ValidationError:
        print("invalid settings. environment variables might not be set.  Run `si-cli validate-settings`")
        logging.basicConfig(
            level=logging.INFO,
            format="{name}|{asctime}|{levelname}|{message}",
            datefmt="%Y-%m-%dT%H:%M:%S",
            style="{",
        )


app = typer.Typer(callback=init)

# ── Sub-apps ──────────────────────────────────────────────────────

batch_app = typer.Typer(help="Batch operations")
document_app = typer.Typer(help="Document operations")
config_app = typer.Typer(help="Configuration operations")
run_group_app = typer.Typer(help="Run group operations")
workflow_app = typer.Typer(help="Workflow operations")
status_app = typer.Typer(help="Status operations")
lancedb_app = typer.Typer(help="LanceDB operations")

app.add_typer(batch_app, name="batch")
app.add_typer(document_app, name="document")
app.add_typer(config_app, name="config")
app.add_typer(run_group_app, name="run-group")
app.add_typer(workflow_app, name="workflow")
app.add_typer(status_app, name="status")
app.add_typer(lancedb_app, name="lancedb")


async def _ensure_db():
    from .lib.models import Database

    await Database.initialize()


# ── batch ─────────────────────────────────────────────────────────


async def _batch_list():
    from .lib.operations import list_batches

    await _ensure_db()
    batches = await list_batches()
    table = Table(title="Batches")
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("source", style="green")
    table.add_column("start_date")
    table.add_column("completed_date")
    table.add_column("duration")
    for b in batches:
        table.add_row(
            str(b.id),
            b.name,
            b.source,
            str(b.start_date) if b.start_date else "",
            str(b.completed_date) if b.completed_date else "",
            f"{b.duration:.1f}s" if b.duration else "",
        )
    print(table)


@batch_app.command("list")
def batch_list():
    """List all batches."""
    asyncio.run(_batch_list())


# ── document ──────────────────────────────────────────────────────


async def _document_list(source: str | None, batch_id: int | None):
    from .lib.operations import get_uris_for_batch
    from .lib.operations import get_uris_for_source

    await _ensure_db()
    if source:
        uris = await get_uris_for_source(source)
    elif batch_id is not None:
        uris = await get_uris_for_batch(batch_id)
    else:
        print("[red]Either --source or --batch-id is required[/red]")
        raise typer.Exit(code=1)

    table = Table(title="Document URIs")
    table.add_column("id", style="cyan")
    table.add_column("doc_hash")
    table.add_column("uri")
    table.add_column("source", style="green")
    table.add_column("version")
    table.add_column("batch_id")
    for u in uris:
        table.add_row(
            str(u.id),
            u.doc_hash[:16] + "…",
            u.uri,
            u.source,
            str(u.version),
            str(u.batch_id) if u.batch_id else "",
        )
    print(table)


@document_app.command("list")
def document_list(
    source: str = typer.Option(None, "--source", help="Source filter"),
    batch_id: int = typer.Option(None, "--batch-id", help="Batch ID filter"),
):
    """List documents by source or batch ID."""
    asyncio.run(_document_list(source, batch_id))


async def _document_find(pattern: str):
    from .lib.operations import find_document_uris_by_pattern

    await _ensure_db()
    uris = await find_document_uris_by_pattern(pattern)
    if not uris:
        print(f"[yellow]No documents found matching '{pattern}'[/yellow]")
        return

    table = Table(title=f"Documents matching '{pattern}'")
    table.add_column("doc_hash")
    table.add_column("batch_id", style="cyan")
    table.add_column("source", style="green")
    table.add_column("uri")
    for u in uris:
        table.add_row(
            u.doc_hash[:16] + "…",
            str(u.batch_id) if u.batch_id else "",
            u.source,
            u.uri,
        )
    print(table)


@document_app.command("find")
def document_find(
    pattern: str = typer.Argument(help="Search pattern for URI"),
):
    """Find document URIs by a string pattern."""
    asyncio.run(_document_find(pattern))


async def _document_info(doc_hash: str):
    from .lib.operations import get_document
    from .lib.operations import get_document_uris_by_hash

    await _ensure_db()
    try:
        doc = await get_document(doc_hash)
    except ValueError:
        print(f"[red]Document {doc_hash} not found[/red]")
        raise typer.Exit(code=1) from None

    print(f"\n[bold]Document:[/bold] {doc.hash}")
    print(f"  mime_type: {doc.mime_type}")
    print(f"  file_size: {doc.file_size}")
    if doc.doc_meta:
        meta_str = json.dumps(doc.doc_meta, indent=2)
        print("  doc_meta:")
        print(Syntax(meta_str, "json", theme="monokai"))

    uris = await get_document_uris_by_hash(doc_hash)
    if uris:
        table = Table(title="Associated URIs")
        table.add_column("id", style="cyan")
        table.add_column("uri")
        table.add_column("source", style="green")
        table.add_column("version")
        table.add_column("batch_id")
        for u in uris:
            table.add_row(
                str(u.id),
                u.uri,
                u.source,
                str(u.version),
                str(u.batch_id) if u.batch_id else "",
            )
        print(table)


@document_app.command("info")
def document_info(
    doc_hash: str = typer.Argument(help="Document hash"),
):
    """Display info for a document by hash."""
    asyncio.run(_document_info(doc_hash))


async def _document_history(doc_hash: str):
    from .lib.operations import get_document_uri_history
    from .lib.operations import get_document_uris_by_hash

    await _ensure_db()
    uris = await get_document_uris_by_hash(doc_hash)
    if not uris:
        print(f"[yellow]No URIs found for hash {doc_hash}[/yellow]")
        return

    table = Table(title=f"History for {doc_hash[:16]}…")
    table.add_column("uri_id", style="cyan")
    table.add_column("uri")
    table.add_column("version")
    table.add_column("hash")
    table.add_column("process_date")
    table.add_column("action", style="green")
    table.add_column("batch_id")
    table.add_column("histmeta")

    for uri in uris:
        history = await get_document_uri_history(uri.id)
        for h in history:
            table.add_row(
                str(h.doc_uri_id),
                uri.uri,
                str(h.version),
                h.hash[:16] + "…",
                str(h.process_date),
                h.action,
                str(h.batch_id) if h.batch_id else "",
                json.dumps(h.histmeta) if h.histmeta else "",
            )
    print(table)


@document_app.command("history")
def document_history(
    doc_hash: str = typer.Argument(help="Document hash"),
):
    """Show DocumentURIHistory records for a document hash."""
    asyncio.run(_document_history(doc_hash))


# ── config ────────────────────────────────────────────────────────


async def _config_workflows():
    from .lib.wf.registry import load_workflow_registry

    registry = await load_workflow_registry()
    table = Table(title="Workflow Definitions")
    table.add_column("id", style="cyan")
    table.add_column("name")
    for wf in registry.values():
        table.add_row(wf.id, wf.name)
    print(table)


@config_app.command("workflows")
def config_workflows():
    """List all workflow definitions."""
    asyncio.run(_config_workflows())


async def _config_params():
    from .lib.wf.registry import load_param_registry

    registry = await load_param_registry()
    table = Table(title="Parameter Sets")
    table.add_column("id", style="cyan")
    table.add_column("name")
    table.add_column("source", style="green")
    for ps in registry.values():
        table.add_row(ps.id, ps.name or "", ps.source)
    print(table)


@config_app.command("params")
def config_params():
    """List all parameter definitions."""
    asyncio.run(_config_params())


async def _config_param_def(param_id: str):
    from .lib.wf.registry import get_param_set_yaml_content

    yaml_content = await get_param_set_yaml_content(param_id)
    if yaml_content is None:
        print(f"[red]Parameter set '{param_id}' not found[/red]")
        raise typer.Exit(code=1)
    print(Syntax(yaml_content, "yaml", theme="monokai"))


@config_app.command("param-def")
def config_param_def(
    param_id: str = typer.Argument(help="Parameter set ID"),
):
    """Display parameter definition YAML."""
    asyncio.run(_config_param_def(param_id))


async def _config_workflow_def(wf_id: str):
    from .lib.wf.registry import get_workflow_definition_yaml_content

    yaml_content = await get_workflow_definition_yaml_content(wf_id)
    if yaml_content is None:
        print(f"[red]Workflow definition '{wf_id}' not found[/red]")
        raise typer.Exit(code=1)
    print(Syntax(yaml_content, "yaml", theme="monokai"))


@config_app.command("workflow-def")
def config_workflow_def(
    wf_id: str = typer.Argument(help="Workflow definition ID"),
):
    """Display workflow definition YAML."""
    asyncio.run(_config_workflow_def(wf_id))


# ── run-group ─────────────────────────────────────────────────────


async def _run_group_list(batch_id: int | None):
    from .lib.wf.operations import get_run_groups_for_batch

    await _ensure_db()
    groups = await get_run_groups_for_batch(batch_id)
    table = Table(title="Run Groups")
    table.add_column("id", style="cyan")
    table.add_column("batch_id")
    table.add_column("created_date")
    table.add_column("workflow_definition_id", style="green")
    table.add_column("param_definition_id")
    table.add_column("status")
    for g in groups:
        table.add_row(
            str(g.id),
            str(g.batch_id) if g.batch_id else "",
            str(g.created_date),
            g.workflow_definition_id,
            g.param_definition_id,
            str(g.status),
        )
    print(table)


@run_group_app.command("list")
def run_group_list(
    batch_id: int = typer.Option(None, "--batch-id", help="Filter by batch ID"),
):
    """List run groups."""
    asyncio.run(_run_group_list(batch_id))


async def _run_group_info(run_group_id: int):
    from .lib.wf.operations import NotFoundError
    from .lib.wf.operations import get_run_group
    from .lib.wf.operations import get_run_group_stats

    await _ensure_db()
    try:
        rg = await get_run_group(run_group_id)
    except NotFoundError:
        print(f"[red]Run group {run_group_id} not found[/red]")
        raise typer.Exit(code=1) from None

    print(f"\n[bold]Run Group:[/bold] {rg.id}")
    print(f"  batch_id: {rg.batch_id}")
    print(f"  workflow_definition_id: {rg.workflow_definition_id}")
    print(f"  param_definition_id: {rg.param_definition_id}")
    print(f"  status: {rg.status}")
    print(f"  created_date: {rg.created_date}")
    print(f"  start_date: {rg.start_date}")
    print(f"  completed_date: {rg.completed_date}")

    stats = await get_run_group_stats(run_group_id)
    total = sum(stats.values())
    table = Table(title="Status Breakdown")
    table.add_column("status", style="cyan")
    table.add_column("count", justify="right")
    for st, count in stats.items():
        table.add_row(str(st), str(count))
    table.add_row("[bold]total[/bold]", f"[bold]{total}[/bold]")
    print(table)


@run_group_app.command("info")
def run_group_info(
    run_group_id: int = typer.Argument(help="Run group ID"),
):
    """Display run group details and status breakdown."""
    asyncio.run(_run_group_info(run_group_id))


# ── workflow ──────────────────────────────────────────────────────


async def _workflow_list(run_group_id: int, status_filter: str | None):
    from .lib.wf.operations import get_document_info_for_workflow_runs
    from .lib.wf.operations import get_workflow_runs_for_group

    await _ensure_db()
    runs = await get_workflow_runs_for_group(run_group_id)
    if status_filter:
        runs = [r for r in runs if r.status == status_filter]

    doc_info = await get_document_info_for_workflow_runs(runs)

    table = Table(title=f"Workflow Runs (run_group={run_group_id})")
    table.add_column("id", style="cyan")
    table.add_column("doc_hash")
    table.add_column("doc_uri")
    table.add_column("status")
    for r in runs:
        di = doc_info.get(r.doc_id)
        table.add_row(
            str(r.id),
            r.doc_id[:16] + "…",
            di.uri if di and di.uri else "",
            str(r.status),
        )
    print(table)


@workflow_app.command("list")
def workflow_list(
    run_group_id: int = typer.Argument(help="Run group ID"),
    status: str = typer.Option(None, "--status", help="Filter by status"),
):
    """List workflow runs for a run group."""
    asyncio.run(_workflow_list(run_group_id, status))


async def _workflow_info(workflow_run_id: int, status_filter: str | None):
    from .lib.wf.operations import NotFoundError
    from .lib.wf.operations import get_workflow_run

    await _ensure_db()
    try:
        run, steps = await get_workflow_run(workflow_run_id, include_steps=True)
    except NotFoundError:
        print(f"[red]Workflow run {workflow_run_id} not found[/red]")
        raise typer.Exit(code=1) from None

    print(f"\n[bold]Workflow Run:[/bold] {run.id}")
    print(f"  doc_id: {run.doc_id}")
    print(f"  status: {run.status}")
    print(f"  status_date: {run.status_date}")
    print(f"  created_date: {run.created_date}")
    print(f"  start_date: {run.start_date}")
    print(f"  completed_date: {run.completed_date}")

    if status_filter:
        steps = [s for s in steps if s.status == status_filter]

    table = Table(title="Run Steps")
    table.add_column("step_type", style="cyan")
    table.add_column("status")
    table.add_column("retry", justify="right")
    table.add_column("status_message")
    for s in steps:
        table.add_row(
            str(s.step_type),
            str(s.status),
            str(s.retry),
            s.status_message or "",
        )
    print(table)


@workflow_app.command("info")
def workflow_info(
    workflow_run_id: int = typer.Argument(help="Workflow run ID"),
    status: str = typer.Option(None, "--status", help="Filter steps by status"),
):
    """Display workflow run info and steps."""
    asyncio.run(_workflow_info(workflow_run_id, status))


# ── status ────────────────────────────────────────────────────────


def _fmt_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.1f}h"


async def _status_running():
    from .lib.wf.operations import get_running_steps_enriched

    await _ensure_db()
    rows = await get_running_steps_enriched()
    if not rows:
        print("[yellow]No running steps[/yellow]")
        return

    table = Table(title="Running Steps")
    table.add_column("workflow_id", style="cyan")
    table.add_column("doc_hash")
    table.add_column("doc_uri")
    table.add_column("run_group")
    table.add_column("param_def_id")
    table.add_column("step_type", style="green")
    table.add_column("started")
    table.add_column("elapsed", justify="right")
    for r in rows:
        table.add_row(
            str(r["workflow_run_id"]),
            r["doc_hash"][:16] + "…" if r["doc_hash"] else "",
            r["doc_uri"] or "",
            str(r["run_group_id"]),
            r["param_definition_id"],
            str(r["step_type"]),
            str(r["start_date"]) if r["start_date"] else "",
            _fmt_elapsed(r["elapsed_seconds"]),
        )
    print(table)


@status_app.command("running")
def status_running():
    """List all run steps in RUNNING status."""
    asyncio.run(_status_running())


async def _status_recent(interval: str, status_filter: str | None):
    from .lib.wf.operations import get_recent_steps

    await _ensure_db()
    status_enum = None
    if status_filter:
        from .lib.models import RunStatus

        try:
            status_enum = RunStatus(status_filter)
        except ValueError:
            print(f"[red]Invalid status '{status_filter}'. Valid: {', '.join(s.value for s in RunStatus)}[/red]")
            raise typer.Exit(code=1) from None

    try:
        rows = await get_recent_steps(interval, status_enum)
    except ValueError as e:
        print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    if not rows:
        print(f"[yellow]No steps in the last {interval}[/yellow]")
        return

    table = Table(title=f"Recent Steps (last {interval})")
    table.add_column("step_type", style="green")
    table.add_column("workflow_id", style="cyan")
    table.add_column("doc_hash")
    table.add_column("doc_uri")
    table.add_column("run_group")
    table.add_column("param_def_id")
    table.add_column("started")
    table.add_column("elapsed", justify="right")
    table.add_column("retry", justify="right")
    table.add_column("status")
    table.add_column("status_message")
    for r in rows:
        table.add_row(
            str(r["step_type"]),
            str(r["workflow_run_id"]),
            r["doc_hash"][:16] + "…" if r["doc_hash"] else "",
            r["doc_uri"] or "",
            str(r["run_group_id"]),
            r["param_definition_id"],
            str(r["start_date"]) if r["start_date"] else "",
            _fmt_elapsed(r["elapsed_seconds"]),
            str(r["retry"]),
            str(r["status"]),
            r["status_message"] or "",
        )
    print(table)


@status_app.command("recent")
def status_recent(
    interval: str = typer.Argument(
        "minute",
        help="Time interval: minute, hour, day, week",
    ),
    status: str = typer.Option(None, "--status", help="Filter by status"),
):
    """List steps with status updates within a time interval."""
    asyncio.run(_status_recent(interval, status))


async def _status_details(run_group_id: int):
    from .lib.wf.operations import get_run_group_details

    await _ensure_db()
    try:
        rows = await get_run_group_details(run_group_id)
    except RuntimeError as e:
        print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    if not rows:
        print(f"[yellow]No non-pending steps for run group {run_group_id}[/yellow]")
        return

    table = Table(title=f"Run Group {run_group_id} Details")
    table.add_column("batch_name")
    table.add_column("param_def_id")
    table.add_column("step_type", style="green")
    table.add_column("status")
    table.add_column("count", justify="right", style="cyan")
    table.add_column("pages", justify="right")
    for r in rows:
        table.add_row(
            str(r[0]),
            str(r[1]),
            str(r[2]),
            str(r[3]),
            str(r[4]),
            str(r[5]) if r[5] is not None else "",
        )
    print(table)


@status_app.command("details")
def status_details(
    run_group_id: int = typer.Argument(help="Run group ID"),
):
    """Display aggregated step details for a run group (PostgreSQL only)."""
    asyncio.run(_status_details(run_group_id))


# ── lancedb ────────────────────────────────────────────────────────


async def _vacuum(db_name: str, sign: bool):
    from .lib.rag import vacuum_db

    await vacuum_db(db_name, sign=sign)


@lancedb_app.command("vacuum")
def lancedb_vacuum(
    db_name: str = typer.Argument(
        help="Name of the LanceDB database to vacuum",
    ),
    sign: bool = typer.Option(
        False,
        "--sign",
        help="Write an HMAC-SHA512 signature after vacuuming (requires LANCEDB_HMAC_KEY)",
    ),
):
    """Vacuum a LanceDB database to reclaim space.

    Removes deleted rows and compacts data files in the specified
    database under the configured lancedb_dir.

    Examples:
        si-diag lancedb vacuum my_database
        si-diag lancedb vacuum my_database --sign
    """
    asyncio.run(_vacuum(db_name, sign))


async def _vacuum_all(sign: bool):
    from .lib.rag import vacuum_all

    await vacuum_all(sign=sign)


@lancedb_app.command("vacuum-all")
def lancedb_vacuum_all(
    sign: bool = typer.Option(
        False,
        "--sign",
        help="Write an HMAC-SHA512 signature after vacuuming each database (requires LANCEDB_HMAC_KEY)",
    ),
):
    """Vacuum every LanceDB database under the configured lancedb_dir.

    Iterates over all database directories and vacuums each one.

    Examples:
        si-diag lancedb vacuum-all
        si-diag lancedb vacuum-all --sign
    """
    asyncio.run(_vacuum_all(sign))


@lancedb_app.command("verify")
def lancedb_verify(
    db_name: str = typer.Argument(
        help="Name of the LanceDB database to verify",
    ),
):
    """Verify the HMAC-SHA512 signature of a LanceDB database.

    Compares the stored .hmac file against a freshly computed HMAC
    over all files in the database directory.

    Examples:
        si-diag lancedb verify my_database
    """
    from .lib.rag import verify_db

    try:
        verify_db(db_name)
        print("[bold green]HMAC verification passed[/bold green]")
    except (FileNotFoundError, ValueError) as e:
        print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
