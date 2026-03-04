import hashlib
import logging
from pathlib import Path

from haiku.rag.app import HaikuRAGApp
from haiku.rag.config import get_config

import soliplex.ingester.lib.wf.operations as wf_ops
from soliplex.ingester.lib.config import get_settings
from soliplex.ingester.lib.models import WorkflowRun
from soliplex.ingester.lib.models import WorkflowStepType
from soliplex.ingester.lib.wf.registry import get_param_set
from soliplex.ingester.lib.workflow import WorkflowException

logger = logging.getLogger(__name__)
_start_span_marker = None
_notif_slack_marker = None
_end_span_marker = None


async def start_span(span_id: str):
    global _start_span_marker
    _start_span_marker = span_id
    logger.info(f"starting span {span_id}")


async def end_span(span_id: str):
    global _end_span_marker
    _end_span_marker = span_id
    logger.info(f"end span {span_id}")


async def notify_slack(channel_id: str, msg: str):
    logger.info(f"notify slack {channel_id} {msg}")
    global _notif_slack_marker
    _notif_slack_marker = channel_id


async def validate_document(batch_id: int = None, doc_hash: str = None, source: str = None, fail: bool = False):
    logger.info(f"validate_document started  source={source} batch_id={batch_id} doc_hash={doc_hash}")
    if fail:
        raise WorkflowException("validation failed")

    logger.info(f"validate_document completed  source={source} batch_id={batch_id} doc_hash={doc_hash}")


async def parse_document(batch_id: int = None, doc_hash: str = None, source: str = None):
    logger.info(f"parse_document started  source={source} batch_id={batch_id} doc_hash={doc_hash}")


async def chunk_document(batch_id: int = None, doc_hash: str = None, source: str = None):
    logger.info(f"chunk_document started  source={source} batch_id={batch_id} doc_hash={doc_hash}")


async def embed_document(batch_id: int = None, doc_hash: str = None, source: str = None):
    logger.info(f"embed_document started  source={source} batch_id={batch_id} doc_hash={doc_hash}")


async def save_document(
    batch_id: int = None,
    doc_hash: str = None,
    uri: str = None,
    source: str = None,
):
    logger.info(f"save_document started  source={source} batch_id={batch_id} doc_hash={doc_hash}")


def create_app(db: Path | None = None, read_only: bool = False) -> HaikuRAGApp:
    """Create HaikuRAGApp with loaded config and resolved database path.

    Args:
        db: Optional database path. If None, uses path from config.

    Returns:
        HaikuRAGApp instance with proper config and db path.
    """
    config = get_config()
    config.storage.vacuum_retention_seconds = 0
    db_path = db if db else config.storage.data_dir / "haiku.rag.lancedb"
    return HaikuRAGApp(
        db_path=db_path,
        config=config,
        read_only=read_only,
    )


async def _vacuum_db(db_path):
    logger.info(f"vacuuming db {db_path}")
    app = create_app(db_path)
    await app.vacuum()


def build_hash(db_path: Path):
    sha256 = hashlib.sha256(usedforsecurity=True)
    [sha256.update(x.read_bytes()) for x in db_path.rglob("*") if x.is_file()]
    hash = sha256.hexdigest()
    hashpath = db_path.parent / f"{db_path.name}.sha256"
    logger.info(f"writing hash {hash} to {hashpath}")
    hashpath.write_text(hash)


async def run_end(workflow_run: WorkflowRun = None):
    """
    Sample function called at the end of a run to vacuum the database. It also stores a hash of the database
    to help identify if the database has changed due to command line operations, etc.

    Args:
        workflow_run: WorkflowRun instance to get the run group id from. provided by the workflow engine
    """
    logger.info(f"starting run_end for run group {workflow_run.run_group_id}")
    run_group = await wf_ops.get_run_group(workflow_run.run_group_id)
    params = await get_param_set(run_group.param_definition_id)
    db_name = params.config[WorkflowStepType.STORE]["data_dir"]
    settings = get_settings()
    lancedb_dir = settings.lancedb_dir
    db_path = Path(lancedb_dir) / db_name
    await _vacuum_db(db_path)
    build_hash(db_path)
