import asyncio
import logging
import os
import platform
import selectors
import shutil
import signal
import sys
from pathlib import Path

import typer
import uvicorn
import uvicorn.config
from pydantic_core import ValidationError
from rich import print

import soliplex.ingester

from .lib.config import get_settings

logger = logging.getLogger(__name__)


def _windows_selector_loop_factory():
    """Create a SelectorEventLoop on Windows for psycopg async PostgreSQL compatibility."""
    selector = selectors.SelectSelector()
    return asyncio.SelectorEventLoop(selector)


def init():
    # Set Windows-compatible event loop policy for psycopg async PostgreSQL driver
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        logging.basicConfig(level=get_settings().log_level)
    except ValidationError:
        print("invalid settings. environment variables might not be set.  Run `si-cli validate-settings`")
        logging.basicConfig(level=logging.INFO)


app = typer.Typer(callback=init)


@app.command("validate-settings")
def validate_settings(dump=True):
    """
    dump application settings
    """
    try:
        get_settings()
    except ValidationError as e:
        print("invalid settings. environment variables not set?")
        for x in e.errors():
            print(x)
        sys.exit(1)
    if dump:
        settings = get_settings()
        print(settings)


def signal_handler(signum, frame):
    logger.info(f"Signal {signum} received, shutting down")
    sys.exit(0)


def run_migrations():
    pass

    # alembic_cfg = Config("alembic.ini")
    # command.upgrade(alembic_cfg, "head")


@app.command("db-init")
def db_init():
    """
    initialize database tables
    """
    validate_settings(dump=False)
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    settings = get_settings()
    engine = create_engine(settings.doc_db_url)
    SQLModel.metadata.create_all(engine)
    # run_migrations()


def export_to_env(file_path: str):
    """
    Export a pydantic_settings model to a .env file.

    Args:

        file_path: The path to the .env file.
    """
    from soliplex.ingester.lib.config import Settings

    if os.path.exists(file_path):
        print(f"{file_path} already exists. remove or choose a different path.")
        return
    print("initializing environment with default sqlite db docs.db")
    model = Settings(doc_db_url="sqlite+aiosqlite:///docs.db")
    model.artifact_s3 = None
    model.input_s3 = None
    with open(file_path, "w") as f:
        for field_name in Settings.model_fields.keys():
            value = getattr(model, field_name)

            # Convert field name to uppercase for environment variable convention
            env_var_name = field_name.upper()

            # Handle None values
            if value is None:
                continue

            # Convert value to string representation
            if isinstance(value, bool):
                value_str = str(value).lower()
            elif isinstance(value, (int, float)):
                value_str = str(value)
            elif isinstance(value, str):
                # Escape quotes and handle multiline strings
                if '"' in value or "\n" in value or " " in value:
                    value_str = f'"{value.replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"')}"'
                else:
                    value_str = value
            else:
                # For other types (lists, dicts, nested models), use JSON representation
                import json

                value_str = json.dumps(value)

            f.write(f"{env_var_name}={value_str}\n")


@app.command("init-env")
def init_env(cfg_name: str = ".env"):
    """
    initialize environment file
    """
    print("initializing environment...")
    export_to_env(cfg_name)


@app.command("init-haiku")
def init_haiku():
    print("initializing haiku...")
    import haiku.rag.cli as haiku_rag_cli

    if os.path.exists("haiku.rag.yaml"):
        print("haiku.rag.yaml already exists. remove or choose a different path.")
        return
    haiku_rag_cli.init_config(Path("haiku.rag.yaml"))


@app.command("init-config")
def init_config():
    print("initializing  default config files...")
    wf_path = Path(".") / "config" / "workflows"
    param_path = Path(".") / "config" / "params"
    wf_path.mkdir(parents=True, exist_ok=True)
    param_path.mkdir(parents=True, exist_ok=True)

    import soliplex.ingester.example as ex

    shutil.copyfile(Path(ex.__file__).parent / "default_wf.yaml", wf_path / "default_wf.yaml")
    shutil.copyfile(Path(ex.__file__).parent / "default_params.yaml", param_path / "default.yaml")


@app.command("bootstrap")
def bootstrap(haiku: bool = True, config: bool = True, env: bool = True):
    print("starting bootstrap")
    if haiku:
        init_haiku()
    if config:
        init_config()
    if env:
        init_env()

    print("bootstrap complete")


async def _start_worker():
    from .lib.models import Database
    from .lib.wf.runner import start_worker

    # Initialize database before starting worker
    await Database.initialize()

    await start_worker()
    # sleep forever
    while True:
        await asyncio.sleep(1)


@app.command("worker")
def worker_cmd():
    """
    Run the Soliplex-ingester worker
    """
    validate_settings(dump=False)
    signal.signal(signal.SIGINT, signal_handler)
    asyncio.run(_start_worker())


async def _dump_workflow(workflow_def_id: str):
    from .lib.wf.registry import get_workflow_definition

    wf_def = await get_workflow_definition(workflow_def_id)
    print(wf_def.model_dump_json(indent=2))


@app.command("dump-workflow")
def dump_workfkow(workflow_def_id: str):
    """
    dump a workflow definition from config
    """
    validate_settings(dump=False)
    asyncio.run(_dump_workflow(workflow_def_id))


async def _dump_params(param_def_id: str):
    from .lib.wf.registry import get_param_set

    wf_def = await get_param_set(param_def_id)
    print(wf_def.model_dump_json(indent=2))


@app.command("dump-param-set")
def dump_param(param_def_id: str = "default"):
    """
    dump a parameter set from config
    """
    validate_settings(dump=False)
    asyncio.run(_dump_params(param_def_id))


async def _list_workflows():
    from .lib.wf.registry import load_workflow_definition

    settings = get_settings()
    wf_ids = []
    for p in Path(settings.workflow_dir).glob("*.yaml"):
        try:
            wf = await load_workflow_definition(p)
            wf_ids.append(wf.id)
        except ValidationError as ve:
            print(f"invalid workflow {p}: ")
            for e in ve.errors():
                print(f"loc: {e['loc']}\t msg: {e['msg']}")
            return

    for wf in wf_ids:
        print(wf)


@app.command("list-workflows")
def list_workflows():
    """list configured workflows"""
    validate_settings(dump=False)
    asyncio.run(_list_workflows())


async def _list_params():
    from .lib.wf.registry import load_param_set

    settings = get_settings()
    set_ids = []
    for p in Path(settings.param_dir).glob("*.yaml"):
        try:
            pset = await load_param_set(p)
            set_ids.append(pset.id)
        except ValidationError as ve:
            print(f"invalid param set {p}: ")
            for e in ve.errors():
                print(f"loc: {e['loc']}\t msg: {e['msg']}")
            return

    for wf in set_ids:
        print(wf)


@app.command("list-param-sets")
def list_params():
    """list configured parameter sets"""
    validate_settings(dump=False)
    asyncio.run(_list_params())


async def _list_batches():
    from .lib.operations import list_batches

    batches = await list_batches()
    for b in batches:
        print(b.id, b.name, b.source)


@app.command("list-batches")
def list_batches():
    validate_settings(dump=False)
    asyncio.run(_list_batches())


async def _check_db(db_name: str, lancedb_dir: str | None):
    from .lib.models import Database
    from .lib.operations import check_rag_db_consistency

    await Database.initialize()
    result = await check_rag_db_consistency(db_name, lancedb_dir)

    print("\n[bold]Database Consistency Check[/bold]")
    print(f"  db_name:     {result['db_name']}")
    print(f"  lancedb_dir: {result['lancedb_dir']}")
    print(f"  db_path:     {result['db_path']}")

    if "error" in result:
        print(f"\n[red]Error:[/red] {result['error']}")

    print("\n[bold]Summary:[/bold]")
    print(f"  DocumentDB records: {result['documentdb_count']}")
    print(f"  LanceDB documents:  {result['lancedb_count']}")
    print(f"  Matched:            {result['matched']}")

    if result["in_documentdb_only"]:
        print(f"\n[yellow]In DocumentDB but NOT in LanceDB ({len(result['in_documentdb_only'])}):[/yellow]")
        for doc in result["in_documentdb_only"]:
            print(f"  - rag_id: {doc['rag_id']}")
            print(f"    uri:    {doc['uri']}")
            print(f"    hash:   {doc['doc_hash']}")
    else:
        print("\n[green]No documents in DocumentDB missing from LanceDB[/green]")

    if result["in_lancedb_only"]:
        print(f"\n[yellow]In LanceDB but NOT in DocumentDB ({len(result['in_lancedb_only'])}):[/yellow]")
        for doc in result["in_lancedb_only"]:
            print(f"  - rag_id: {doc['rag_id']}")
            print(f"    uri:    {doc.get('uri', 'N/A')}")
            print(f"    title:  {doc.get('title', 'N/A')}")
    else:
        print("\n[green]No documents in LanceDB missing from DocumentDB[/green]")

    if not result["in_documentdb_only"] and not result["in_lancedb_only"] and "error" not in result:
        print("\n[green]✓ Database is consistent[/green]")


@app.command("check-db")
def check_db(
    db_name: str = typer.Argument(..., help="Database name (data_dir) to check"),
    lancedb_dir: str = typer.Option(None, "--lancedb-dir", "-l", help="LanceDB directory (uses default if not specified)"),
):
    """
    Check consistency between LanceDB database and DocumentDB records.

    Compares documents in the actual LanceDB database with records tracked
    in DocumentDB to find discrepancies.
    """
    validate_settings(dump=False)
    asyncio.run(_check_db(db_name, lancedb_dir))


@app.command(
    "serve",
)
def serve(
    ctx: typer.Context,
    host: str = typer.Option(
        "127.0.0.1",
        "-h",
        "--host",
        help="Bind socket to this host",
    ),
    port: int = typer.Option(
        8000,
        "-p",
        "--port",
        help="Port number",
    ),
    uds: str = typer.Option(
        None,
        "--uds",
        help="Bind to a Unix domain socket",
    ),
    fd: int = typer.Option(
        None,
        "--fd",
        help="Bind to socket from this file descriptor",
    ),
    reload: bool = typer.Option(
        False,
        "-r",
        "--reload",
        help="Reload on file changes",
    ),
    workers: int = typer.Option(
        None,
        "--workers",
        envvar="WEB_CONCURRENCY",
        help="Number of worker processes. Defaults to the "
        "$WEB_CONCURRENCY environment variable if available, or 1. "
        "Not valid with --reload.",
    ),
    access_log: bool | None = typer.Option(
        None,
        "--access-log",
        help="Enable/Disable access log",
    ),
    proxy_headers: bool = typer.Option(
        None,
        "--proxy-headers",
        help=("Enable/Disable X-Forwarded-Proto, X-Forwarded-For to populate url scheme and remote address info."),
    ),
    forwarded_allow_ips: str = typer.Option(
        None,
        "--forwarded-allow-ips",
        envvar="FORWARDED_ALLOW_IPS",
        help="Comma separated list of IP Addresses, IP Networks, or "
        "literals (e.g. UNIX Socket path) to trust with proxy headers. "
        "Defaults to the $FORWARDED_ALLOW_IPS environment "
        "variable if available, or '127.0.0.1'. "
        "The literal '*' means trust everything.",
    ),
):
    from soliplex.ingester import server

    """Run the Soliplex server"""
    validate_settings(dump=False)
    reload_dirs = []
    reload_includes = []

    if reload:
        reload_dirs.extend(soliplex.ingester.__path__)
        reload_includes.append("*.yaml")
        reload_includes.append("*.yml")
        reload_includes.append("*.txt")

    uvicorn_kw = {
        "host": host,
        "port": port,
    }

    # Use SelectorEventLoop on Windows for psycopg async PostgreSQL compatibility
    if platform.system() == "Windows":
        uvicorn_kw["loop"] = _windows_selector_loop_factory

    if uds is not None:
        uvicorn_kw["uds"] = uds

    if fd is not None:
        uvicorn_kw["fd"] = fd

    if workers is not None:
        uvicorn_kw["workers"] = workers

    if access_log is not None:
        uvicorn_kw["access_log"] = access_log

    if proxy_headers is not None:
        uvicorn_kw["proxy_headers"] = proxy_headers

    if forwarded_allow_ips is not None:
        uvicorn_kw["forwarded_allow_ips"] = forwarded_allow_ips

    if reload or workers:
        uvicorn.run(
            "soliplex.ingester.server:app",
            factory=False,
            reload=reload,
            reload_dirs=reload_dirs,
            reload_includes=reload_includes,
            **uvicorn_kw,
        )
    else:
        app = server.app
        uvicorn.run(app, **uvicorn_kw)


if __name__ == "__main__":
    app()
