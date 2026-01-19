import asyncio
import copy
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

from . import models
from .config import get_settings
from .models import StepConfig

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
    # TODO: step config may need to be more predictable
    docling_document = DoclingDocument.model_validate_json(docling_json)
    config = build_storage_config(config, config_dict)

    # TODO: decide what to do about doc title/uri
    # title = doc_hash  # we don't have a title yet
    title = None
    uri = source_uri.uri
    source = source_uri.source

    meta = doc.doc_meta.copy()

    meta["doc_id"] = doc_hash
    meta["md5"] = md5_hash
    meta["content_type"] = doc.mime_type
    db_path = None
    if "lancedb_dir" in config_dict:
        db_path = pathlib.Path(config_dict["lancedb_dir"])

    meta["source"] = source
    # FIXME: move create to batch start
    # lock writes to avoid concurrent writes
    logger.info(f"bytes docling={len(docling_json)}", extra=_log_con)
    async with _rag_lock:
        async with HaikuRAG(config=config, create=True, db_path=db_path) as client:
            # try to find the document
            found = _find_docs_by_hash(doc_hash, client.document_repository.store.documents_table)
            if found and len(found) != 0:
                doc_id = found[0].id
                # delete the document
                await client.delete_document(doc_id)

            new_doc = await client.import_document(
                chunks=chunks,
                title=title,
                uri=uri,
                metadata=meta,
                docling_document=docling_document,
            )
        return new_doc.id
