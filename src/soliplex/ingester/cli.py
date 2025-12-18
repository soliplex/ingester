import asyncio
import json
import logging
import platform
import signal
import sys

import typer
import uvicorn
import uvicorn.config
from haiku.rag.client import HaikuRAG
from pydantic_core import ValidationError
from rich import print

import soliplex.ingester
import soliplex.ingester.lib.models as models
import soliplex.ingester.lib.operations as operations
from soliplex.ingester import server

from .lib.config import get_settings
from .lib.wf.registry import get_param_set
from .lib.wf.registry import get_workflow_definition
from .lib.wf.registry import load_param_registry
from .lib.wf.registry import load_workflow_registry
from .lib.wf.runner import start_worker

logger = logging.getLogger(__name__)


def init():
    validate_settings(dump=False)
    logging.basicConfig(level=get_settings().log_level)


app = typer.Typer(callback=init)


@app.command("validate-settings")
def validate_settings(dump=True):
    """
    dump application settings
    """
    try:
        get_settings()
    except ValidationError as e:
        print("invalid settings")
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
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")


@app.command("db-init")
def db_init():
    """
    initialize database tables
    """
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel

    settings = get_settings()
    engine = create_engine(settings.doc_db_url)
    SQLModel.metadata.create_all(engine)
    run_migrations()


async def _validate_haiku(batch_id: int, detail: bool = False):
    docs = await operations.get_documents_in_batch(batch_id)
    results = []
    read_doc_bytes = operations.read_doc_bytes
    async with HaikuRAG() as client:
        for doc in docs:
            doc_hash = doc.hash
            try:
                _ = await read_doc_bytes(
                    doc_hash, models.ArtifactType.PARSED_MD
                )
            except Exception as e:
                results.append(
                    {
                        "doc": doc.hash,
                        "haiku": doc.rag_id,
                        "message": str(e),
                        "status": "md error",
                    }
                )
                continue

            if doc.rag_id is None:
                results.append(
                    {
                        "doc": doc.hash,
                        "haiku": None,
                        "message": "no haiku found",
                        "status": "no_id",
                    }
                )
                continue

            try:
                hrdoc = await client.get_document_by_id(doc.rag_id)
                results.append(
                    {"doc": doc.hash, "haiku": hrdoc.id, "status": "success"}
                )
            except Exception as e:
                results.append(
                    {
                        "doc": doc.hash,
                        "haiku": None,
                        "message": str(e),
                        "status": "haiku error",
                    }
                )
                continue
    fails = [x for x in results if x["status"] != "success"]
    print("-----------results --------------")
    print(f" found {len(results)} results")
    print("-----------fails --------------")
    print(json.dumps(fails, indent=4))


@app.command("validate-haiku")
def validate_haiku(batch_id: int, detail: bool = False):
    """
    checks haiku-rag status for a batch
    """
    asyncio.run(_validate_haiku(batch_id, detail))


async def _start_worker():
    await start_worker()
    # sleep forever
    while True:
        await asyncio.sleep(1)


@app.command("worker")
def worker_cmd():
    """
    Run the Soliplex-ingester worker
    """
    signal.signal(signal.SIGINT, signal_handler)
    asyncio.run(_start_worker())


async def _dump_workflow(workflow_def_id: str):
    wf_def = await get_workflow_definition(workflow_def_id)
    print(wf_def.model_dump_json(indent=2))


@app.command("dump-workflow")
def dump_workfkow(workflow_def_id: str):
    """
    dump a workflow definition from config
    """
    asyncio.run(_dump_workflow(workflow_def_id))


async def _dump_params(param_def_id: str):
    wf_def = await get_param_set(param_def_id)
    print(wf_def.model_dump_json(indent=2))


@app.command("dump-param-set")
def dump_param(param_def_id: str = "default"):
    """
    dump a parameter set from config
    """
    asyncio.run(_dump_params(param_def_id))


async def _list_workflows():
    workflows = await load_workflow_registry()
    for wf in workflows.keys():
        print(wf)


@app.command("list-workflows")
def list_workflows():
    """list configured workflows"""
    asyncio.run(_list_workflows())


async def _list_params():
    params = await load_param_registry()
    for wf in params.keys():
        print(wf)


@app.command("list-param-sets")
def list_params():
    """list configured parameter sets"""
    asyncio.run(_list_params())


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
        help=(
            "Enable/Disable X-Forwarded-Proto, X-Forwarded-For to "
            "populate url scheme and remote address info."
        ),
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
    """Run the Soliplex server"""
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
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    app()
