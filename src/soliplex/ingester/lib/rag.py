import asyncio
import copy
import hashlib
import hmac
import itertools
import logging
import pathlib
import uuid
from contextlib import asynccontextmanager

from docling_core.types.doc.document import DoclingDocument
from haiku.rag.app import HaikuRAGApp
from haiku.rag.chunkers import get_chunker
from haiku.rag.client import HaikuRAG
from haiku.rag.config import Config as HRConfig
from haiku.rag.config import get_config
from haiku.rag.config.models import AppConfig
from haiku.rag.embeddings import embed_chunks
from haiku.rag.store.engine import DocumentRecord
from haiku.rag.store.exceptions import MigrationRequiredError
from haiku.rag.store.models.chunk import Chunk

from . import models
from .config import get_settings
from .models import ResourceLockKind
from .models import StepConfig

logger = logging.getLogger(__name__)


def resource_key_for(db_path: pathlib.Path | str) -> str:
    """Stable, opaque resource_key for a LanceDB. Used as the
    primary key into ``resourcelock`` so every writer of this DB —
    workflow ``save_to_rag`` steps, web vacuum endpoint, ``si-diag``
    vacuum, ``end_group`` lifecycle vacuums — coordinates against
    the same row."""
    return f"rag:{pathlib.Path(db_path)}"


@asynccontextmanager
async def hold_rag_lock(
    db_path: pathlib.Path | str,
    holder_kind: ResourceLockKind,
    *,
    ttl_seconds: int = 600,
    poll_interval: float = 1.0,
    max_wait: float | None = None,
    holder_meta: dict[str, str] | None = None,
):
    """Acquire the cross-subsystem RAG-DB lock for the duration of a
    block. If the lock is held by someone else, waits up to
    *max_wait* seconds (default: forever) before raising
    :class:`TimeoutError`.

    Used by every direct-from-Python RAG writer (CLI vacuum, lifecycle
    vacuum). Workflow workers go through ``operations.claim_next_step``
    which already acquires the lock at claim time, so they don't
    need this wrapper.
    """
    # Local import to avoid the runner importing rag at import time
    # (rag imports haiku-rag which is heavy).
    from .wf import operations as wf_ops

    key = resource_key_for(db_path)
    holder_id = f"{holder_kind.value}:{uuid.uuid4()}"
    deadline = None if max_wait is None else asyncio.get_event_loop().time() + max_wait
    while True:
        got = await wf_ops.acquire_resource_lock(
            key,
            holder_id=holder_id,
            holder_kind=holder_kind,
            ttl_seconds=ttl_seconds,
            holder_meta=holder_meta,
        )
        if got:
            break
        if deadline is not None and asyncio.get_event_loop().time() > deadline:
            current = await wf_ops.get_resource_lock(key)
            who = (
                f"{current.holder_kind}:{current.holder_id} since {current.acquired_at}" if current is not None else "unknown"
            )
            raise TimeoutError(f"RAG DB locked by {who}")
        await asyncio.sleep(poll_interval)
    try:
        yield holder_id
    finally:
        try:
            await wf_ops.release_resource_lock(key, holder_id)
        except Exception:
            logger.exception("failed to release rag lock %s", key)


def build_docling_config(start_config: AppConfig, config_dict: dict[str, str | int | bool]) -> AppConfig:
    config = copy.deepcopy(start_config)
    env = get_settings()
    # may cause issues if they go to v2
    config.providers.docling_serve.base_url = env.docling_chunk_server_url.replace("/v1", "")

    return config


def build_embed_config(start_config: AppConfig, config_dict: dict[str, str | int | bool]) -> AppConfig:
    config = copy.deepcopy(start_config)
    env = get_settings()
    required_keys = ["model", "vector_dim"]
    for key in required_keys:
        if key not in config_dict:
            raise ValueError(f"Missing required key {key}")
    # for k, v in config_dict.items():
    #    setattr(config.embeddings, k, v)
    config.embeddings.model.name = config_dict["model"]
    config.embeddings.model.vector_dim = config_dict["vector_dim"]
    config.embeddings.model.provider = config_dict["provider"]
    # force haiku to use env variable even if config has a value set
    config.providers.ollama.base_url = env.ollama_base_url
    return config


def build_chunk_config(start_config: AppConfig, config_dict: dict[str, str | int | bool]) -> AppConfig:
    config = copy.deepcopy(start_config)
    # config = build_docling_config(config, config_dict)
    env = get_settings()
    # may cause issues if they go to v2
    config.providers.docling_serve.base_url = env.docling_chunk_server_url.replace("/v1", "")

    # turn off ocr for chunking as it's already been done and can cause problems in FIPS environments
    config.processing.conversion_options.ocr_engine = "rapidocr"
    config.processing.conversion_options.do_ocr = False
    config.processing.conversion_options.force_ocr = False
    # some
    if "text_context_radius" in config_dict:
        del config_dict["text_context_radius"]
    required_keys = ["chunk_size", "chunker"]
    for key in required_keys:
        if key not in config_dict:
            raise ValueError(f"Missing required key {key}")
    for k, v in config_dict.items():
        setattr(config.processing, k, v)

    return config


def build_storage_config(start_config: AppConfig, config_dict: dict[str, str | int | bool]) -> AppConfig:
    env = get_settings()
    config = copy.deepcopy(start_config)
    required_keys = ["data_dir"]
    for key in required_keys:
        if key not in config_dict:
            raise ValueError(f"Missing required key {key}")
    for k, v in config_dict.items():
        setattr(config.storage, k, v)
    storage_dir = config_dict["data_dir"]
    logger.info(f"param storage_dir: {storage_dir}")
    if storage_dir.startswith("s3://"):
        config.lancedb.uri = storage_dir
        config.lancedb.api_key = "xxx"  # these just need to be filled in, environment variables have the real value
        config.lancedb.region = "xx"
        config.storage.data_dir = pathlib.Path(storage_dir)
        logger.info(f"hr lancedb uri: {config.lancedb.uri}")
    elif env.lancedb_dir.startswith("s3://"):
        if env.lancedb_dir.endswith("/"):
            s3_dir = f"{env.lancedb_dir}{storage_dir}"
        else:
            s3_dir = f"{env.lancedb_dir}/{storage_dir}"
        config.lancedb.uri = s3_dir
        config.lancedb.api_key = "xxx"  # these just need to be filled in, environment variables have the real value
        config.lancedb.region = "xx"
        config.storage.data_dir = pathlib.Path(s3_dir)
        logger.info(f"hr lancedb uri: {config.lancedb.uri}")
    else:
        config.storage.data_dir = pathlib.Path(env.lancedb_dir) / pathlib.Path(storage_dir)
        logger.info(f"hr storage data dir: {config.storage.data_dir}")
    config.storage.auto_vacuum = False  # hardcode to be off as it causes too many issues
    return config


def build_full_config(
    start_config: AppConfig,
    chunk_config: dict[str, str | int | bool],
    embed_config: dict[str, str | int | bool],
    storage_config: dict[str, str | int | bool],
):
    """
    Build a full haiku rag config using configuration chunks
    """
    config = copy.deepcopy(start_config)
    config = build_chunk_config(config, chunk_config)
    config = build_embed_config(config, embed_config)
    config = build_storage_config(config, storage_config)
    return config


async def get_chunk_objs(
    docling_document: DoclingDocument,
    config_dict: dict[str, str | int | bool],
) -> list[Chunk]:
    config = build_chunk_config(HRConfig, config_dict)
    chunker = get_chunker(config)
    chunks = await chunker.chunk(docling_document)
    if len(chunks) == 0:
        raise ValueError("No chunks found ")
    return chunks


async def embed(
    chunks: list[Chunk],
    config_dict: dict[str, str | int | bool],
    doc_hash: str,
) -> list[Chunk]:
    env = get_settings()
    config = build_embed_config(HRConfig, config_dict)
    ret = []
    # don't use gather to avoid overloading ollama
    for batch in itertools.batched(chunks, n=env.embed_batch_size, strict=False):
        batch_chunks = await embed_chunks(batch, config)
        logger.info(f"{doc_hash}embedded {len(batch_chunks)} chunks of {len(chunks)} total")
        ret.extend(batch_chunks)
    return ret


def resolve_lancedb_path(step_config: StepConfig) -> str:
    env = get_settings()
    config_dict = step_config.config_json
    db_path = pathlib.Path(env.lancedb_dir) / config_dict["data_dir"]
    return db_path


def resolve_lancedb_path_from_param_config(
    store_config: dict[str, str | int | bool],
) -> pathlib.Path:
    """Resolve LanceDB path from a param set's store config."""
    env = get_settings()
    data_dir = store_config["data_dir"]
    return pathlib.Path(env.lancedb_dir) / data_dir


def _find_docs_by_hash(doc_hash: str, tbl) -> list[DocumentRecord]:
    return tbl.search().where(f"metadata like '%{doc_hash}%'").to_pydantic(DocumentRecord)


async def check_rag_existence(
    doc_hashes: list[str],
    store_config: dict[str, str | int | bool],
    embed_config: dict[str, str | int | bool],
) -> set[str]:
    """Return set of doc_hashes already present in the target RAG DB.

    Args:
        doc_hashes: List of document SHA256 hashes to check.
        store_config: Store section from param set config.
        embed_config: Embed section from param set config.

    Returns:
        Set of doc_hashes that already exist in the RAG database.
    """
    db_path = resolve_lancedb_path_from_param_config(store_config)
    if not db_path.exists():
        logger.info(f"RAG DB does not exist at {db_path}, nothing to skip")
        return set()

    config = build_embed_config(HRConfig, embed_config)
    config = build_storage_config(config, store_config)

    found = set()
    # LanceDB read-only access is concurrent-safe — no lock needed.
    async with HaikuRAG(config=config, read_only=True, create=False, db_path=db_path) as client:
        tbl = client.document_repository.store.documents_table
        for h in doc_hashes:
            docs = _find_docs_by_hash(h, tbl)
            if docs:
                found.add(h)
    logger.info(f"pre-check: {len(found)}/{len(doc_hashes)} already in RAG at {db_path}")
    return found


async def save_to_rag(
    doc: models.Document,
    chunks: list[Chunk],
    docling_json: str,
    source_uri: models.DocumentURI,
    step_config: StepConfig,
    embed_config: StepConfig,
    _log_con=None,
):
    md5_hash = doc.doc_meta["md5"]
    doc_hash = doc.hash

    config_dict = step_config.config_json
    config = build_embed_config(HRConfig, embed_config.config_json)
    required_keys = ["data_dir"]
    for key in required_keys:
        if key not in config_dict:
            raise ValueError(f"Missing required key {key}")

    docling_document = DoclingDocument.model_validate_json(docling_json)
    config = build_storage_config(config, config_dict)

    title = None
    if doc.doc_meta and "title" in doc.doc_meta:
        title = doc.doc_meta["title"]

    uri = source_uri.uri
    source = source_uri.source

    meta = doc.doc_meta.copy()

    meta["doc_id"] = doc_hash
    meta["md5"] = md5_hash
    meta["content_type"] = doc.mime_type
    db_path = resolve_lancedb_path(step_config)

    meta["source"] = source
    logger.info(f"bytes docling={len(docling_json)}", extra=_log_con)
    # Per-DB serialization is enforced by the workflow's claim layer
    # via the resource_key on the step (operations.claim_next_step
    # holds a ResourceLock for the duration of execution). Direct
    # callers from outside the workflow should wrap themselves in
    # :func:`hold_rag_lock` instead.
    async with HaikuRAG(config=config, create=True, db_path=db_path) as client:
        found = _find_docs_by_hash(doc_hash, client.document_repository.store.documents_table)
        if found and len(found) != 0:
            logger.info(f"Found existing document {found[0].id}", extra=_log_con)
            doc_id = found[0].id
            await client.delete_document(doc_id)
            logger.debug(f"deleted existing document {found[0].id}", extra=_log_con)

        new_doc = await client.import_document(
            chunks=chunks,
            title=title,
            uri=uri,
            metadata=meta,
            docling_document=docling_document,
        )
    return new_doc.id


def _compute_db_hmac(
    db_path: pathlib.Path,
    key: bytes,
    buf_size: int = 8 * 1024 * 1024,
) -> str:
    """Compute HMAC-SHA512 over all files in a LanceDB database directory.

    Files are read in buffered chunks to avoid loading entire files
    into memory at once.

    Args:
        db_path: Path to the database directory.
        key: HMAC key (must be 64 bytes).
        buf_size: Read buffer size in bytes (default 8 MiB).

    Returns:
        Hex-encoded HMAC digest.
    """
    hm = hmac.new(key, digestmod=hashlib.sha512)
    for f in sorted(db_path.rglob("*")):
        if f.is_file():
            with open(f, "rb") as fh:
                while True:
                    chunk = fh.read(buf_size)
                    if not chunk:
                        break
                    hm.update(chunk)
    return hm.hexdigest()


def sign_db(db_name: str):
    """Write an HMAC-SHA512 signature file for a LanceDB database.

    Args:
        db_name: Name of the database directory under lancedb_dir.
    """
    env = get_settings()
    key = env.lancedb_hmac_key.get_secret_value().encode()
    if len(key) != 64:
        raise ValueError("LANCEDB_HMAC_KEY must be 64 bytes")
    db_path = pathlib.Path(env.lancedb_dir) / db_name
    hhash = _compute_db_hmac(db_path, key)
    hashpath = db_path.parent / f"{db_path.name}.hmac"
    logger.info(f"writing hmac {hhash} to {hashpath}")
    hashpath.write_text(hhash)


def verify_db(db_name: str) -> bool:
    """Verify the HMAC-SHA512 signature of a LanceDB database.

    Args:
        db_name: Name of the database directory under lancedb_dir.

    Returns:
        True if the signature matches.

    Raises:
        FileNotFoundError: If the .hmac file does not exist.
        ValueError: If the signature does not match.
    """
    env = get_settings()
    key = env.lancedb_hmac_key.get_secret_value().encode()
    if len(key) != 64:
        raise ValueError("LANCEDB_HMAC_KEY must be 64 bytes")
    db_path = pathlib.Path(env.lancedb_dir) / db_name
    hashpath = db_path.parent / f"{db_path.name}.hmac"
    if not hashpath.exists():
        raise FileNotFoundError(f"No HMAC file found at {hashpath}")
    expected = hashpath.read_text().strip()
    actual = _compute_db_hmac(db_path, key)
    if not hmac.compare_digest(expected, actual):
        raise ValueError(f"HMAC mismatch for {db_path}: expected {expected}, got {actual}")
    return True


def _resolve_db_path(db_name: str) -> pathlib.Path:
    """Resolve a LanceDB database path, checking for haiku.rag.lancedb subfolder.

    Args:
        db_name: Name of the database directory under lancedb_dir.

    Returns:
        The resolved path to the actual LanceDB database.

    Raises:
        FileNotFoundError: If no database exists at the resolved path.
    """
    env = get_settings()
    db_path = pathlib.Path(env.lancedb_dir) / db_name
    subdir = db_path / "haiku.rag.lancedb"
    if subdir.exists():
        logger.info(f"found haiku.rag.lancedb subfolder in {db_path}")
        db_path = subdir
    if not db_path.exists():
        raise FileNotFoundError(f"Database does not exist at {db_path}")
    return db_path


async def vacuum_db(
    db_name: str,
    sign: bool = False,
    holder_kind: ResourceLockKind = ResourceLockKind.CLI,
    max_wait: float | None = None,
):
    """Vacuum a LanceDB database to reclaim space.

    Acquires the cross-subsystem :class:`ResourceLock` for the DB
    before opening it, so vacuums can never race with workflow
    ``save_to_rag`` writers, the web vacuum endpoint, or other
    vacuum runs.

    If the database requires a migration, it will be run automatically
    before vacuuming. If a haiku.rag.lancedb subfolder exists inside
    the named directory, that subfolder is vacuumed instead.

    Args:
        db_name: Name of the database directory under lancedb_dir.
        sign: If True, write an HMAC signature after vacuuming.
        holder_kind: Identifies the caller (``cli``, ``web``,
            ``lifecycle``) in the lock row for observability.
        max_wait: Seconds to wait for the lock; ``None`` waits
            forever. Callers that prefer fail-fast behavior (e.g.
            ``si-diag``) should pass ``0``.

    Raises:
        FileNotFoundError: If the database does not exist.
        TimeoutError: If the lock could not be acquired within
            *max_wait* seconds.
    """
    db_path = _resolve_db_path(db_name)
    logger.info(f"vacuuming db {db_path}")
    async with hold_rag_lock(db_path, holder_kind=holder_kind, max_wait=max_wait):
        config = get_config()
        config.storage.vacuum_retention_seconds = 0
        app = HaikuRAGApp(
            db_path=db_path,
            config=config,
            read_only=False,
        )
        try:
            await app.vacuum()
        except MigrationRequiredError:
            logger.info(f"migration required for {db_path}, running migrate first")
            applied = app.migrate()
            for desc in applied:
                logger.info(f"applied migration: {desc}")
            await app.vacuum()
        if sign:
            sign_db(db_name)


def list_dbs() -> list[str]:
    """List database names found under the configured lancedb_dir.

    A directory is considered a database if it is itself a LanceDB
    directory (contains .lance files/tables) or contains a
    haiku.rag.lancedb subfolder.

    Returns:
        Sorted list of database directory names.
    """
    env = get_settings()
    base = pathlib.Path(env.lancedb_dir)
    if not base.exists():
        return []
    db_names = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        # skip .hmac sidecar files that share the name
        if child.suffix == ".hmac":
            continue
        subdir = child / "haiku.rag.lancedb"
        if subdir.exists() and subdir.is_dir():
            db_names.append(child.name)
        elif any(child.iterdir()):
            # non-empty directory — treat as a db
            db_names.append(child.name)
    return db_names


async def vacuum_all(sign: bool = False):
    """Vacuum every database under the configured lancedb_dir.

    Args:
        sign: If True, write an HMAC signature after vacuuming each db.
    """
    db_names = list_dbs()
    if not db_names:
        logger.info("no databases found to vacuum")
        return
    for name in db_names:
        try:
            await vacuum_db(name, sign=sign)
        except Exception:
            logger.exception(f"failed to vacuum {name}")
