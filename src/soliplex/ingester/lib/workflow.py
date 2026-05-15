import asyncio
import hashlib
import json
import logging
import time
from io import BytesIO
from pathlib import Path

import aiofiles
import pypdf
from docling_core.types.doc.document import DoclingDocument
from haiku.rag.store.models.chunk import Chunk

from . import models
from . import operations as doc_ops
from . import rag
from .config import get_settings
from .docling import docling_convert
from .models import ArtifactType
from .models import StepConfig
from .models import WorkflowStepType
from .operations import log_context
from .processing import find_title
from .wf import operations as wf_ops

logger = logging.getLogger(__name__)

MIN_PDF_PAGES = 10
MAX_PDF_PAGES = 30


class WorkflowException(Exception):
    pass


async def initial_load(
    source_uri: str,
    source: str,
    doc_meta: dict[str, str],
    batch_id: str = None,
    file_bytes: bytes = None,
    input_uri: str = None,
    mime_type: str = None,
) -> tuple[models.DocumentURI, models.Document]:
    docuri, doc = await doc_ops.create_document_from_uri(
        source_uri,
        source,
        mime_type=mime_type,
        doc_meta=doc_meta,
        file_bytes=file_bytes,
        input_uri=input_uri,
        batch_id=batch_id,
    )
    return docuri, doc


async def create_doc_workflow(
    batch_id: int,
    doc_hash: str,
    source: str,
    workflow_definition_id: str | None = None,
    param_set_id: str | None = None,
    priority: int = 0,
):
    wfrun, wf_steps = await wf_ops.create_workflow_run(
        workflow_definition_id=workflow_definition_id,
        batch_id=batch_id,
        doc_id=doc_hash,
        param_id=param_set_id,
        priority=priority,
    )


async def _get_op(
    workflow_run_id: int,
    step_type: WorkflowStepType,
    artifact_type: ArtifactType = None,
):
    if artifact_type is None:
        artifact_type = models.ARTIFACTS_FROM_STEPS[step_type]
        if isinstance(artifact_type, list):
            msg = f"multiple artifact types for step_type {step_type}"
            raise ValueError(msg)
    op = await wf_ops.find_operator_for_workflow_run(workflow_run_id, step_type, artifact_type)
    return op


async def read_bytes(
    doc_hash: str,
    workflow_run_id: int,
    step_type: WorkflowStepType,
    artifact_type: ArtifactType = None,
):
    op = await _get_op(workflow_run_id, step_type, artifact_type)
    res = await op.read(doc_hash)
    return res


async def validate_document(
    batch_id: int = None,
    doc_hash: str = None,
    source: str = None,
    step_config: StepConfig = None,
):
    """
    basic checks
    """
    start = time.time()
    logger.info(f"validate_document started  source={source} batch_id={batch_id} doc_hash={doc_hash}")
    doc = await doc_ops.get_document(doc_hash)
    if doc:
        logger.debug(f"validate_document found  source={source} batch_id={batch_id} doc_hash={doc_hash}")
    else:
        raise doc_ops.DocumentNotFoundError(doc_hash)
    meta = doc.doc_meta

    if doc.mime_type == "application/pdf":
        try:
            file_bytes = await doc_ops.read_doc_bytes(doc_hash, ArtifactType.DOC)
            fp = BytesIO(file_bytes)

            pdfdoc = pypdf.PdfReader(fp)
            meta["is_valid"] = True
            meta["invalid_reason"] = None
            meta["page_count"] = len(pdfdoc.pages)
            for k in ["/Author", "/Subject", "/Title", "/Keywords", "/Subject"]:
                if pdfdoc.metadata:
                    v = pdfdoc.metadata.get(k)
                    if v is not None:
                        cleaned_key = "pdf_" + k.lower().replace("/", "")
                        meta[cleaned_key] = v
            fp.close()
            del file_bytes
            del pdfdoc
        except Exception as e:
            meta["is_valid"] = False
            meta["invalid_reason"] = str(e)
        await doc_ops.update_doc_meta(doc_hash, meta)
    else:
        # no validation methods for other stuff at this point
        meta["is_valid"] = True

    if not meta["is_valid"]:
        msg = f"{doc_hash} invalid: {meta['invalid_reason']}"
        raise doc_ops.DocumentInvalidError(msg)

    logger.info(
        f"validate_document found valid  source={source} batch_id={batch_id} doc_hash={doc_hash} took {time.time() - start}"
    )
    return


async def read_file(path: Path, mode="rb"):
    async with aiofiles.open(path, mode) as f:
        return await f.read()


async def split_parse_document(
    batch_id: int = None,
    doc_hash: str = None,
    source: str = None,
    step_config: StepConfig = None,
    workflow_run: models.WorkflowRun = None,
    force: bool = False,
    file_bytes_override: bytes = None,
    mime_type_override: str = None,
    markdown_override: str = None,
):
    """
    splits document into pieces using pdf_splitter , parses each piece then combines the results
    into one docling document.  stores markdown and docling json documents to storage.
    if the document isn't a pdf, it delegates to the single-piece pipeline.
    if file_bytes_override is provided, it will be used instead of reading from storage.
    If markdown_override is provided, it will be used instead of using the generated markdown.
    This works around issues with docling

    Args:
        batch_id (int): batch id
        doc_hash (str): document hash
        source (str): source of document
        step_config (StepConfig): step config
        workflow_run (models.WorkflowRun): workflow run
        file_bytes_override (bytes): file bytes to override
        mime_type_override (str): mime type to override
        markdown_override (str): markdown override for parsed md

    Returns:
        None
    """
    _lc = log_context(doc_hash=doc_hash, batch_id=batch_id, action="split_parse")
    start = time.time()
    logger.info(f"parse_document started  {source} {batch_id} {doc_hash}", extra=_lc)
    doc_uris = await doc_ops.get_document_uris_by_hash(doc_hash)
    if len(doc_uris) == 0:
        msg = f"no uris found for {doc_hash}"
        raise WorkflowException(msg)
    test_op = await _get_op(workflow_run.id, WorkflowStepType.PARSE, ArtifactType.PARSED_JSON)
    exists = await test_op.exists(doc_hash)
    logger.info(
        f"do parse {doc_hash} {exists} {force}",
        extra=log_context(doc_hash=doc_hash, batch_id=batch_id, action="do_parse"),
    )
    source_uri = doc_uris[0].uri
    if not source_uri.endswith(".pdf"):
        logger.info(
            f"sending {doc_hash} to conventional processing (not a pdf) {source_uri}",
            extra=log_context(doc_hash=doc_hash, batch_id=batch_id, action="do_parse"),
        )
        await parse_document(
            batch_id=batch_id,
            doc_hash=doc_hash,
            source=source_uri,
            force=force,
            step_config=step_config,
            workflow_run=workflow_run,
            file_bytes_override=file_bytes_override,
            mime_type_override=mime_type_override,
            markdown_override=markdown_override,
        )
        return

    test_op_json = await _get_op(workflow_run.id, WorkflowStepType.PARSE, ArtifactType.PARSED_JSON)
    test_op_md = await _get_op(workflow_run.id, WorkflowStepType.PARSE, ArtifactType.PARSED_MD)
    exists = await test_op_json.exists(doc_hash) and await test_op_md.exists(doc_hash)
    logger.info(
        f"do parse split {doc_hash} {exists} {force}",
        extra=_lc,
    )
    if exists and not force:
        logger.info(f"skipping parse for {doc_hash} already exists", extra=_lc)
        return
    if file_bytes_override is not None:
        file_bytes = file_bytes_override
    else:
        file_bytes = await doc_ops.read_doc_bytes(doc_hash, ArtifactType.DOC)
    file_size = len(file_bytes)
    logger.info(f"starting split_parse file_size={file_size}", extra=_lc)
    markdown_bytes, json_bytes = await split_parse_bytes(
        file_bytes,
        source_uri,
        step_config.config_json,
    )
    del file_bytes
    logger.info(f"document {doc_hash} size={file_size} parsed", extra=_lc)
    jsop = await _get_op(
        workflow_run.id,
        WorkflowStepType.PARSE,
        ArtifactType.PARSED_JSON,
    )
    await jsop.write(doc_hash, json_bytes)
    mdop = await _get_op(workflow_run.id, WorkflowStepType.PARSE, ArtifactType.PARSED_MD)
    await mdop.write(doc_hash, markdown_bytes)
    # check for existence of files, in some cases it's missing
    if not await test_op_json.exists(doc_hash) or not await test_op_md.exists(doc_hash):
        logger.info(f"document {doc_hash} missing files, retrying write", extra=_lc)
        await jsop.write(doc_hash, json_bytes)
        await mdop.write(doc_hash, markdown_bytes)
    json_exists = await test_op_json.exists(doc_hash)
    md_exists = await test_op_md.exists(doc_hash)
    logger.debug(f"document {doc_hash} json={json_exists} md={md_exists}", extra=_lc)
    if not json_exists or not md_exists:
        raise WorkflowException(
            f"document {doc_hash} missing files after retrying write json={json_exists} md={md_exists}",
        )
    await doc_ops.add_history_for_hash(doc_hash, "parsed", batch_id=batch_id)
    logger.info(f"parse_document completed  {source} {batch_id} {doc_hash} took {time.time() - start}", extra=_lc)


async def parse_document(
    batch_id: int = None,
    doc_hash: str = None,
    source: str = None,
    force: bool = False,
    step_config: StepConfig = None,
    workflow_run: models.WorkflowRun = None,
    file_bytes_override: bytes = None,
    mime_type_override: str = None,
    markdown_override: str = None,
):
    """
    parses document using docling  as one piece.  stores markdown and docling json documents to storage
    if file_bytes_override is provided, it will be used instead of reading from storage.
    If markdown_override is provided, it will be used instead of using the generated markdown.
    This works around issues with docling

    Args:
        batch_id (int): batch id
        doc_hash (str): document hash
        source (str): source of document
        force (bool): force the parse even if it already exists
        step_config (StepConfig): step config
        workflow_run (models.WorkflowRun): workflow run
        file_bytes_override (bytes): file bytes to override
        mime_type_override (str): mime type to override
        markdown_override (str): markdown override for parsed md

    Returns:
        None
    """
    logger.info(f"parse_document started  {source} {batch_id} {doc_hash}")
    start = time.time()
    doc = await doc_ops.get_document(doc_hash)
    test_op = await _get_op(workflow_run.id, WorkflowStepType.PARSE, ArtifactType.PARSED_JSON)
    exists = await test_op.exists(doc_hash)
    logger.info(
        f"do parse {doc_hash} {exists} {force}",
        extra=log_context(doc_hash=doc_hash, batch_id=batch_id, action="do_parse"),
    )
    if not exists or force:
        doc_uris = await doc_ops.get_document_uris_by_hash(doc_hash)
        if len(doc_uris) == 0:
            msg = f"no uris found for {doc_hash}"
            raise WorkflowException(msg)
        source_uri = doc_uris[0].uri
        if file_bytes_override is None:
            file_bytes = await doc_ops.read_doc_bytes(doc_hash, ArtifactType.DOC)
        else:
            file_bytes = file_bytes_override
        # mime_type_override allows overriding mime type by pre-processors such as asciidoc or plantnuml
        mime_type = mime_type_override if mime_type_override is not None else doc.mime_type
        try:
            md_bytes, json_bytes = await parse_bytes(
                file_bytes,
                mime_type,
                source_uri,
                step_config.config_json,
            )
        except WorkflowException:
            logger.exception(
                f"parse failed for {doc_hash} {doc.mime_type} {source_uri}",
                extra=log_context(
                    doc_hash=doc_hash,
                    batch_id=batch_id,
                    action="do_parse",
                ),
            )
            raise
        if markdown_override is not None:
            logger.info(f"using markdown override for {mime_type} /{doc_hash}")
            md_bytes = markdown_override.encode("utf-8") if isinstance(markdown_override, str) else markdown_override
        for st, content in (
            (ArtifactType.PARSED_JSON, json_bytes),
            (ArtifactType.PARSED_MD, md_bytes),
        ):
            op = await _get_op(workflow_run.id, WorkflowStepType.PARSE, st)
            if force:
                try:
                    await op.delete(doc_hash)
                except FileNotFoundError:
                    pass
            await op.write(doc_hash, content)
        for artifact_type in (
            ArtifactType.PARSED_JSON,
            ArtifactType.PARSED_MD,
        ):
            check_op = await _get_op(
                workflow_run.id,
                WorkflowStepType.PARSE,
                artifact_type,
            )
            if not await check_op.exists(doc_hash):
                msg = f"parse {doc_hash}: artifact {artifact_type} missing after write"
                logger.error(
                    msg,
                    extra=log_context(
                        doc_hash=doc_hash,
                        batch_id=batch_id,
                        action="do_parse",
                    ),
                )
                raise WorkflowException(msg)
        await doc_ops.add_history_for_hash(doc_hash, "parsed", batch_id=batch_id)
    else:
        logger.info(
            f"skipping parse for doc={doc_hash} exists={exists} force={force}",
            extra=log_context(doc_hash=doc_hash, batch_id=batch_id, action="do_parse"),
        )

    logger.info(f"parse_document completed  {source} {batch_id} {doc_hash} took{time.time() - start:.2f}s")


async def parse_bytes(
    file_bytes: bytes,
    mime_type: str,
    source_uri: str,
    config: dict | None = None,
) -> tuple[bytes, bytes]:
    """Convert document bytes via docling. Returns (markdown_bytes, docling_json_bytes).

    Standalone kernel — no storage, workflow, or document-tracking handling.
    Used by ``parse_document`` and the path-based ``parse_pdf_file`` wrapper.
    """
    config = config or {}
    parsed = await docling_convert(
        file_bytes,
        mime_type,
        source_uri=source_uri,
        config_dict=config,
    )
    if not parsed:
        msg = f"failed to parse {source_uri} ({mime_type})"
        raise WorkflowException(msg)
    missing = {"json", "md"} - parsed.keys()
    if missing:
        msg = f"parse {source_uri}: missing expected formats {missing}"
        raise WorkflowException(msg)
    md = parsed["md"]
    js = parsed["json"]
    if isinstance(md, str):
        md = md.encode("utf-8")
    if isinstance(js, str):
        js = js.encode("utf-8")
    return md, js


async def parse_pdf_file(
    pdf_path: Path,
    config: dict | None = None,
) -> tuple[bytes, bytes]:
    """Parse a single PDF file via docling. Returns (markdown_bytes, docling_json_bytes).

    Path-based wrapper around ``parse_bytes`` — reads the file then delegates.
    """
    file_bytes = await read_file(pdf_path)
    return await parse_bytes(file_bytes, "application/pdf", str(pdf_path), config)


async def split_parse_pdf_file(
    pdf_path: Path,
    config: dict | None = None,
    source_uri: str | None = None,
) -> tuple[bytes, bytes]:
    """Split a PDF, parse pieces via docling, and merge. Returns (markdown_bytes, docling_json_bytes).

    Standalone kernel — no storage, workflow, or document-tracking handling.
    Honors ``split_workers`` and ``use_serve`` keys in ``config``.
    ``source_uri`` defaults to ``str(pdf_path)`` and is forwarded to docling
    for source-tracking metadata.
    """
    from pdf_splitter.processor import BatchProcessor
    from pdf_splitter.reassembly import merge_from_results
    from pdf_splitter.segmentation_enhanced import smart_split_to_files

    config = config or {}
    if source_uri is None:
        source_uri = str(pdf_path)
    split_workers = config.get("split_workers", 1)
    use_serve = config.get("use_serve", True)

    async with aiofiles.tempfile.TemporaryDirectory() as temp_dir:
        tf = Path(temp_dir)
        split_result = smart_split_to_files(pdf_path, output_dir=tf, parallel=False, max_chunk_pages=30, min_chunk_pages=10)
        if len(split_result) != 2:
            msg = f"split_to_files returned {split_result}.  should return 2 items.  {source_uri}"
            raise WorkflowException(msg)
        split_files = split_result[0]
        logger.info(f"split_parse {source_uri} split into {len(split_files)} pieces")

        if use_serve:
            convert_sem = asyncio.Semaphore(12)

            async def _read_and_convert(split_path):
                async with convert_sem:
                    fb = await read_file(split_path)
                    result = await docling_convert(
                        fb,
                        "application/pdf",
                        source_uri=source_uri,
                        config_dict=config,
                    )
                    return {
                        "success": True,
                        "document_dict": json.loads(result["json"].decode("utf-8")),
                    }

            proc_results = await asyncio.gather(*[_read_and_convert(sp) for sp in split_files])
        else:
            start1 = time.time()
            proc = BatchProcessor(max_workers=split_workers, verbose=True)
            proc_results = proc.execute_parallel(split_files)
            logger.info(f"batch processing took {time.time() - start1:.2f}s files={len(split_files)}")

        docling_doc = merge_from_results(proc_results)
    return (
        docling_doc.export_to_markdown().encode("utf-8"),
        docling_doc.model_dump_json().encode("utf-8"),
    )


async def split_parse_bytes(
    file_bytes: bytes,
    source_uri: str,
    config: dict | None = None,
) -> tuple[bytes, bytes]:
    """Split PDF bytes, parse pieces via docling, merge. Returns (markdown_bytes, docling_json_bytes).

    Bytes-based wrapper around ``split_parse_pdf_file`` — writes the bytes to
    a temp file then delegates.
    """
    async with aiofiles.tempfile.TemporaryDirectory() as temp_dir:
        outfile = Path(temp_dir) / "input.pdf"
        async with aiofiles.open(outfile, "wb") as f:
            await f.write(file_bytes)
        return await split_parse_pdf_file(outfile, config=config, source_uri=source_uri)


async def chunk_document(
    batch_id: int = None,
    doc_hash: str = None,
    source: str = None,
    force: bool = False,
    step_config: StepConfig = None,
    workflow_run: models.WorkflowRun = None,
):
    logger.info(f"chunk_document started  {source} {batch_id} {doc_hash}")
    start = time.time()
    op = await _get_op(workflow_run.id, WorkflowStepType.CHUNK, ArtifactType.CHUNKS)
    exists = await op.exists(doc_hash)
    if not exists or force:
        json_bytes = await read_bytes(
            doc_hash,
            workflow_run.id,
            WorkflowStepType.PARSE,
            ArtifactType.PARSED_JSON,
        )
        json_text = json_bytes.decode("utf-8")
        del json_bytes
        docling_document = DoclingDocument.model_validate_json(json_text)
        del json_text

        chunk_objs = await rag.get_chunk_objs(docling_document, step_config.config_json)
        del docling_document
        chunk_dicts = [x.model_dump() for x in chunk_objs]
        del chunk_objs
        chunk_json = json.dumps(chunk_dicts)
        del chunk_dicts
        chunk_bytes = chunk_json.encode("utf-8")
        del chunk_json
        if force:
            try:
                await op.delete(doc_hash)
            except FileNotFoundError:
                pass
        await op.write(doc_hash, chunk_bytes)
        if not await op.exists(doc_hash):
            msg = f"chunk {doc_hash}: artifact {ArtifactType.CHUNKS} missing after write"
            logger.error(
                msg,
                extra=log_context(
                    doc_hash=doc_hash,
                    batch_id=batch_id,
                    action="chunk",
                ),
            )
            raise WorkflowException(msg)
    await doc_ops.add_history_for_hash(doc_hash, "chunked", batch_id=batch_id)

    logger.info(
        f"chunk_document completed  {source} {batch_id} {doc_hash} took {time.time() - start:.2f}s",
        extra=log_context(doc_hash=doc_hash, batch_id=batch_id, action="chunk"),
    )


async def embed_document(
    batch_id: int = None,
    doc_hash: str = None,
    source: str = None,
    force: bool = False,
    step_config: StepConfig = None,
    workflow_run: models.WorkflowRun = None,
):
    _lc = log_context(doc_hash=doc_hash, batch_id=batch_id, action="embed")
    start = time.time()
    logger.info(f"embed_document started  {source} {batch_id} {doc_hash}", extra=_lc)
    check_op = await _get_op(workflow_run.id, WorkflowStepType.CHUNK, ArtifactType.EMBEDDINGS)
    exists = await check_op.exists(doc_hash)
    if not exists or force:
        chunk_op = await _get_op(workflow_run.id, WorkflowStepType.CHUNK, ArtifactType.CHUNKS)
        chunk_bytes = await chunk_op.read(doc_hash)
        chunk_json = chunk_bytes.decode("utf-8")
        del chunk_bytes
        chunk_dicts = json.loads(chunk_json)
        del chunk_json
        chunk_objs = [Chunk.model_validate(x) for x in chunk_dicts]
        del chunk_dicts
        logger.info(
            f"got {len(chunk_objs)} chunks {source} {batch_id} {doc_hash}",
            extra=_lc,
        )
        embed_chunks = await rag.embed(chunk_objs, step_config.config_json, doc_hash=doc_hash)
        del chunk_objs
        embed_op = await _get_op(workflow_run.id, WorkflowStepType.EMBED, ArtifactType.EMBEDDINGS)
        embed_json = json.dumps([x.model_dump() for x in embed_chunks])
        del embed_chunks
        embed_bytes = embed_json.encode("utf-8")
        del embed_json
        await embed_op.write(doc_hash, embed_bytes)
        if not await embed_op.exists(doc_hash):
            msg = f"embed {doc_hash}: artifact {ArtifactType.EMBEDDINGS} missing after write"
            logger.error(msg, extra=_lc)
            raise WorkflowException(msg)

    await doc_ops.add_history_for_hash(doc_hash, "embedded", batch_id=batch_id)

    logger.info(f"embed_document completed  {source} {batch_id} {doc_hash} took {time.time() - start:.2f}s", extra=_lc)


async def save_to_rag(
    batch_id: int = None,
    doc_hash: str = None,
    source: str = None,
    force: bool = False,
    step_config: StepConfig = None,
    workflow_run: models.WorkflowRun = None,
):
    start = time.time()
    logger.info(f"save_to_rag started  {source} {batch_id} {doc_hash}")
    settings = get_settings()
    doc = await doc_ops.get_document(doc_hash)
    _log_con = log_context(
        doc_hash=doc_hash,
        batch_id=batch_id,
        action="save to rag",
        source=source,
    )
    if not settings.do_rag and not force:
        logger.info(
            f"skipping ingestion for {doc_hash} do_rag={settings.do_rag} force={force}",  # noqa: E501
            extra=_log_con,
        )
        return
    chunk_op = await _get_op(workflow_run.id, WorkflowStepType.CHUNK, ArtifactType.CHUNKS)
    embed_op = await _get_op(workflow_run.id, WorkflowStepType.EMBED, ArtifactType.EMBEDDINGS)

    embed_exists = await embed_op.exists(doc_hash)
    if embed_exists:
        chunk_bytes = await embed_op.read(doc_hash)
        logger.debug(f"got embeddings for {doc_hash}", extra=_log_con)
    else:
        chunk_bytes = await chunk_op.read(doc_hash)
        logger.debug(f"got just chunks for {doc_hash}", extra=_log_con)

    chunk_json = chunk_bytes.decode("utf-8")
    chunk_dicts = json.loads(chunk_json)
    chunk_objs = [Chunk.model_validate(x) for x in chunk_dicts]
    r = asyncio.gather(
        read_bytes(
            doc_hash,
            workflow_run.id,
            WorkflowStepType.PARSE,
            ArtifactType.PARSED_JSON,
        ),
        doc_ops.get_document_uris_by_hash(doc_hash),
        wf_ops.get_step_config_for_workflow_run(workflow_run.id, WorkflowStepType.EMBED),
        read_bytes(
            doc_hash,
            workflow_run.id,
            WorkflowStepType.PARSE,
            ArtifactType.PARSED_MD,
        ),
    )

    json_bytes, doc_uris, embed_config, md_bytes = await r
    # older docs don't have md5
    if "md5" not in doc.doc_meta:
        raw_bytes = await read_bytes(
            doc_hash,
            workflow_run.id,
            WorkflowStepType.INGEST,
            ArtifactType.DOC,
        )
        md5_hash = hashlib.md5(raw_bytes, usedforsecurity=False).hexdigest()
        doc.doc_meta["md5"] = md5_hash
    md5_hash = doc.doc_meta["md5"]
    file_size = doc.file_size
    docling_json = json_bytes.decode("utf-8")
    md_content = md_bytes.decode("utf-8")
    find_title(doc, md_content)

    if len(doc_uris) == 0:
        logger.warning(
            f"skipping ingestion for {doc_hash} no uris found in db",
            extra=_log_con,
        )
        return
    source_uri = doc_uris[0]
    logger.debug(
        f"saving to rag: {doc_hash} batch={batch_id}  file_size={file_size} uri={source_uri}",  # noqa: E501
        extra=_log_con,
    )
    rag_id = await rag.save_to_rag(
        doc,
        chunk_objs,
        docling_json,
        source_uri,
        step_config,
        embed_config,
        _log_con,
    )
    await doc_ops.add_history_for_hash(doc_hash, "ingested", hist_meta={"haiku_id": rag_id}, batch_id=batch_id)
    logger.info(f"save_to_rag completed  {source} {batch_id} {doc_hash} took {time.time() - start:.2f}s", extra=_log_con)


async def route_document(
    batch_id: int = None,
    doc_hash: str = None,
    uri: str = None,
    source: str = None,
    step_config: StepConfig = None,
    workflow_run: models.WorkflowRun = None,
):
    logger.info(f"route_document started  {source} {batch_id} {doc_hash}")
    # FIXME: do something

    logger.info(f"route_document completed  {source} {batch_id} {doc_hash} ")
