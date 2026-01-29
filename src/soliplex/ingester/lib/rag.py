import asyncio
import copy
import datetime
import itertools
import logging
import pathlib

from docling_core.types.doc.document import DoclingDocument
from haiku.rag.chunkers import get_chunker
from haiku.rag.client import HaikuRAG
from haiku.rag.config import Config as HRConfig
from haiku.rag.config.models import AppConfig
from haiku.rag.embeddings import embed_chunks
from haiku.rag.store.engine import DocumentRecord
from haiku.rag.store.models.chunk import Chunk
from sqlalchemy import delete as sa_delete

from . import models
from .config import get_settings
from .models import DocumentDB
from .models import StepConfig
from .models import get_session

logger = logging.getLogger(__name__)

_rag_lock = asyncio.Lock()


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
    config = build_docling_config(config, config_dict)
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


def _find_docs_by_hash(doc_hash: str, tbl) -> list[DocumentRecord]:
    return tbl.search().where(f"metadata like '%{doc_hash}%'").to_pydantic(DocumentRecord)


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
    # FIXME: move create to batch start
    # lock writes to avoid concurrent writes
    logger.info(f"bytes docling={len(docling_json)}", extra=_log_con)
    async with _rag_lock:
        async with HaikuRAG(config=config, create=True, db_path=db_path) as client:
            # try to find the document
            found = _find_docs_by_hash(doc_hash, client.document_repository.store.documents_table)
            if found and len(found) != 0:
                logger.info(f"Found existing document {found[0].id}", extra=_log_con)
                doc_id = found[0].id
                # delete the document
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


async def create_document_db_record(
    doc_hash: str,
    source: str,
    step_config: StepConfig,
    rag_id: str,
    chunk_count: int,
) -> DocumentDB:
    """
    Create a DocumentDB record to track a document stored in HaikuRAG.

    Parameters
    ----------
    doc_hash : str
        The document hash (sha256)
    source : str
        The source system identifier
    step_config : StepConfig
        The step configuration containing data_dir
    rag_id : str
        The ID returned by HaikuRAG after import
    chunk_count : int
        Number of chunks stored for this document

    Returns
    -------
    DocumentDB
        The created DocumentDB record
    """
    env = get_settings()
    config_dict = step_config.config_json
    db_name = config_dict.get("data_dir", "")
    lancedb_dir = env.lancedb_dir

    async with get_session() as session:
        record = DocumentDB(
            doc_hash=doc_hash,
            source=source,
            db_name=db_name,
            lancedb_dir=lancedb_dir,
            rag_id=rag_id,
            chunk_count=chunk_count,
            created_date=datetime.datetime.now(datetime.UTC),
        )
        session.add(record)
        await session.flush()
        await session.refresh(record)
        session.expunge(record)
        # session commits automatically on context exit
    return record


async def delete_from_rag_by_hash(doc_hash: str) -> dict[str, int]:
    """
    Delete all HaikuRAG entries and DocumentDB records for a document hash.

    Queries DocumentDB for all records matching the hash, deletes each
    document from its respective HaikuRAG database, then removes the
    DocumentDB records.

    Parameters
    ----------
    doc_hash : str
        The document hash to delete

    Returns
    -------
    dict[str, int]
        Statistics containing:
        - deleted_rag_entries: Number of HaikuRAG documents deleted
        - deleted_documentdb_records: Number of DocumentDB records deleted
    """
    deleted_rag_entries = 0
    deleted_documentdb_records = 0

    async with get_session() as session:
        # Find all DocumentDB records for this hash
        from sqlmodel import select

        q = select(DocumentDB).where(DocumentDB.doc_hash == doc_hash)
        result = await session.exec(q)
        records = result.all()

        for record in records:
            # Reconstruct the db_path
            if record.lancedb_dir.startswith("s3://"):
                if record.lancedb_dir.endswith("/"):
                    db_path = f"{record.lancedb_dir}{record.db_name}"
                else:
                    db_path = f"{record.lancedb_dir}/{record.db_name}"
            else:
                import pathlib

                db_path = pathlib.Path(record.lancedb_dir) / record.db_name

            # Try to delete from HaikuRAG
            if record.rag_id:
                try:
                    # Build minimal config for deletion
                    config = build_embed_config(HRConfig, {"model": "dummy", "vector_dim": 1, "provider": "ollama"})
                    config = build_storage_config(config, {"data_dir": record.db_name})

                    async with _rag_lock:
                        async with HaikuRAG(config=config, create=False, db_path=db_path) as client:
                            await client.delete_document(record.rag_id)
                            deleted_rag_entries += 1
                            logger.info(f"Deleted document {record.rag_id} from HaikuRAG at {db_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete document {record.rag_id} from HaikuRAG at {db_path}: {e}")

        # Delete all DocumentDB records for this hash
        if records:
            delete_q = sa_delete(DocumentDB).where(DocumentDB.doc_hash == doc_hash)
            delete_result = await session.exec(delete_q)
            deleted_documentdb_records = delete_result.rowcount  # type: ignore

        # session commits automatically on context exit

    return {
        "deleted_rag_entries": deleted_rag_entries,
        "deleted_documentdb_records": deleted_documentdb_records,
    }
