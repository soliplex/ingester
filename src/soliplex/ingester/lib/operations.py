import datetime
import hashlib
import logging
import mimetypes
import os

from sqlalchemy import delete
from sqlmodel import select

from . import dal
from . import models
from . import rag
from .config import get_settings

logger = logging.getLogger(__name__)
MIME_OVERRIDES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

MIME_OVERRIDES_REV = {v: k for k, v in MIME_OVERRIDES.items()}


def log_context(
    batch_id: int | None = None,
    doc_hash: str | None = None,
    action: str | None = None,
    source: str | None = None,
    uri: str | None = None,
) -> dict[str, str]:
    return {
        "batch_id": str(batch_id),
        "doc_hash": doc_hash,
        "action": action,
        "source": source,
        "uri": uri,
    }


class DocumentNotFoundError(ValueError):
    def __init__(self, doc_hash):
        super().__init__(f"Document {doc_hash} not found")


class DocumentInvalidError(ValueError):
    def __init__(self, doc_hash):
        super().__init__(f"Document {doc_hash} is invalid")


class BatchNotFoundError(ValueError):
    def __init__(self, batch_id):
        super().__init__(f"Batch {batch_id} not found")


class BatchCompletedError(ValueError):
    def __init__(self, batch_id):
        super().__init__(f"Batch {batch_id} already complete")


def _guess_mime_type(uri: str) -> str:
    file_name = os.path.basename(uri)

    guess = mimetypes.guess_type(file_name)[0]
    if guess is None:
        ext = os.path.splitext(file_name)[1]
        return MIME_OVERRIDES_REV.get(ext, "application/octet-stream")
    return guess


def guess_extension(mime_type: str) -> str:
    guess = mimetypes.guess_extension(mime_type)
    if guess is None:
        return MIME_OVERRIDES.get(mime_type, ".bin")
    return guess


def _extract_hash_value(prefixed_hash: str) -> str:
    """Extract hash value from prefixed format.

    Handles formats like 'sha256-abc123' or 'md5:abc123', returning just the hash portion.
    Returns the original string if no prefix separator is found.
    """
    if "-" in prefixed_hash:
        return prefixed_hash.split("-", 1)[1]
    if ":" in prefixed_hash:
        return prefixed_hash.split(":", 1)[1]
    return prefixed_hash


async def update_doc_meta(doc_hash: str, meta: dict[str, str]):
    async with models.get_session() as session:
        q = select(models.Document).where(models.Document.hash == doc_hash)
        exec_rs = await session.exec(q)
        res = exec_rs.first()
        if res:
            res.doc_meta = meta
            session.add(res)
            await session.commit()
        else:
            raise DocumentNotFoundError(doc_hash)


async def find_document_uri(uri: str, source: str) -> models.DocumentURI:
    logger.info(
        f"find document {uri} {source} ",
        extra=log_context(source=source, uri=uri, action="find_document_uri"),
    )
    async with models.get_session() as session:
        q = select(models.DocumentURI).where(models.DocumentURI.source == source).where(models.DocumentURI.uri == uri)
        exec_rs = await session.exec(q)
        res = exec_rs.first()
        if res:
            session.expunge(res)
        return res


async def get_document_uris_by_hash(doc_hash: str) -> list[models.DocumentURI]:
    logger.debug(
        f"get document {doc_hash}",
        extra=log_context(doc_hash=doc_hash, action="get_document_uris_by_hash"),
    )
    async with models.get_session() as session:
        q = select(models.DocumentURI).where(models.DocumentURI.doc_hash == doc_hash)
        rs = await session.exec(q)
        res = rs.all()
        for item in res:
            session.expunge(item)
        return res


async def add_history_for_hash(doc_hash: str, action: str, batch_id=None, hist_meta=None):
    """
    add history for all document uris with a given hash. useful for
    tracking operations that hit the document level
    """
    if hist_meta is None:
        hist_meta = {}
    doc_uris = await get_document_uris_by_hash(doc_hash)
    async with models.get_session() as session:
        for doc_uri in doc_uris:
            await add_history(doc_uri, hist_meta, action, session, batch_id=batch_id)
        await session.commit()


async def add_history(
    doc_uri: models.DocumentURI,
    doc_meta: dict[str, str],
    action: str,
    session,
    batch_id: int = None,
) -> models.DocumentURIHistory:
    """
    add history for a documenturi
    """
    histmeta = doc_meta.copy()
    hist = models.DocumentURIHistory(
        doc_uri_id=doc_uri.id,
        hash=doc_uri.doc_hash,
        action=action,
        version=doc_uri.version,
        histmeta=histmeta,
        process_date=datetime.datetime.now(datetime.UTC),
        batch_id=batch_id,
    )
    session.add(hist)
    await session.flush()
    return hist


async def handle_file(input_uri: str = None, file_bytes: bytes = None) -> tuple[str, int, str]:
    settings = get_settings()
    if file_bytes is None:
        if input_uri is None:
            raise ValueError("input_uri or file_bytes must be provided")
        file_bytes = await dal.read_input_url(input_uri)

    if file_bytes:
        content_hash = models.doc_hash(file_bytes)
        md5_hash = hashlib.md5(file_bytes, usedforsecurity=False).hexdigest()
        logger.debug(
            f"handle file {input_uri} {content_hash}  to {settings.file_store_target}",
            extra=log_context(uri=input_uri, doc_hash=content_hash, action="handle_file"),
        )

        op = dal.get_storage_operator(models.ArtifactType.DOC)
        exists = await op.exists(content_hash)
        if not exists:
            await op.write(content_hash, file_bytes)
        return content_hash, len(file_bytes), md5_hash
    else:
        raise ValueError("file_bytes must be provided")


async def delete_file(doc_hash: str, session):
    """
    Delete all file artifacts for a document across all workflow runs.

    Uses SQLModel ORM with explicit JOINs for cross-database compatibility.

    Parameters
    ----------
    doc_hash : str
        Document hash to delete files for
    session : AsyncSession
        Database session
    """
    # Get all step configs used for this document
    q = (
        select(models.StepConfig)
        .join(models.RunStep, models.RunStep.step_config_id == models.StepConfig.id)
        .join(models.WorkflowRun, models.WorkflowRun.id == models.RunStep.workflow_run_id)
        .where(models.WorkflowRun.doc_id == doc_hash)
    )

    result = await session.exec(q)
    step_configs = result.all()

    for step_config in step_configs:
        for artifact_type in models.ArtifactType:
            op = dal.get_storage_operator(artifact_type, step_config)
            try:
                await op.delete(doc_hash)
            except FileNotFoundError as fe:
                logger.debug(
                    f"file not found  {doc_hash} {fe}",
                    extra=log_context(doc_hash=doc_hash, action="delete_file"),
                )

    await add_history_for_hash(doc_hash, "file deleted")


async def read_doc_bytes(doc_hash: str, storage_type: models.ArtifactType):
    op = dal.get_storage_operator(storage_type)
    return await op.read(doc_hash)


async def create_document_from_uri(
    source_uri: str,  # uri in source system
    source: str,  # source system identifier
    mime_type: str = None,
    file_bytes: bytes = None,  # bytes from file if available
    input_uri: str = None,  # uri to read file from (e.g. s3 or file)
    doc_meta: dict = None,  # metadata
    batch_id: int = None,
) -> tuple[models.DocumentURI, models.Document]:
    if mime_type is None:
        mime_type = _guess_mime_type(source_uri)
    if batch_id is not None:
        batch = await get_batch(batch_id)
        if batch is None:
            raise BatchNotFoundError(batch_id)
        if batch.completed_date is not None:
            raise BatchCompletedError(batch_id)
    # TODO:handle uris
    async with models.get_session() as session:
        # doc.hash = models.doc_hash(doc.file_bytes)
        doc_hash, file_size, md5_hash = await handle_file(input_uri=input_uri, file_bytes=file_bytes)
        if doc_meta is None:
            doc_meta = {}
        doc_meta.update({"md5": md5_hash})
        doc = models.Document(
            hash=doc_hash,
            uri=source_uri,
            source=source,
            mime_type=mime_type,
            file_bytes=file_bytes,
            doc_meta=doc_meta,
            file_size=file_size,
        )

        docuri = models.DocumentURI(uri=source_uri, source=source, doc_hash=doc.hash, batch_id=batch_id)

        # check if doc exists and create if needed
        docq = select(models.Document).where(models.Document.hash == doc.hash)
        docrs = await session.exec(docq)
        existdoc = docrs.first()
        if existdoc:
            logger.info(
                f"document {doc.hash} already exists",
                extra=log_context(
                    doc_hash=doc.hash,
                    action="create_document_from_uri",
                    uri=source_uri,
                    source=source,
                ),
            )
            doc = existdoc
        else:
            session.add(doc)
            await session.flush()
            await session.refresh(doc)
        # check if uri exists and create if needed
        uriq = (
            select(models.DocumentURI).where(models.DocumentURI.uri == source_uri).where(models.DocumentURI.source == source)
        )
        urirs = await session.exec(uriq)
        existdocuri = urirs.first()
        if existdocuri:
            logger.info(
                f"uri {source_uri}/{source} already exists",
                extra=log_context(
                    doc_hash=doc.hash,
                    action="create_document_from_uri",
                    uri=source_uri,
                    source=source,
                ),
            )
            docuri = existdocuri
            # refresh hash
            if existdocuri.doc_hash != doc.hash:
                existdocuri.doc_hash = doc.hash
                existdocuri.version += 1
                session.add(existdocuri)
                await session.flush()
                await session.refresh(existdocuri)
                await add_history(existdocuri, doc_meta, "update", session, batch_id=batch_id)
                docuri = existdocuri
        else:
            session.add(docuri)
            await session.flush()
            await session.refresh(docuri)
            await add_history(docuri, doc_meta, "create", session, batch_id=batch_id)
            logger.info(
                f"created {docuri.id} {docuri.uri} {docuri.source}",
                extra=log_context(
                    doc_hash=doc.hash,
                    action="create_document_from_uri",
                    uri=source_uri,
                    source=source,
                ),
            )
            await session.refresh(docuri)
        await session.refresh(doc)
        await session.refresh(docuri)
        session.expunge(doc)
        session.expunge(docuri)
        await session.commit()

        return docuri, doc


async def get_document_uri_history(
    doc_uri_id: int,
) -> list[models.DocumentURIHistory]:
    logger.debug(f"get history for {doc_uri_id}")
    async with models.get_session() as session:
        q = (
            select(models.DocumentURIHistory)
            .where(models.DocumentURIHistory.doc_uri_id == doc_uri_id)
            .order_by(models.DocumentURIHistory.process_date.asc())
        )
        rs = await session.exec(q)
        res = rs.all()
        for item in res:
            session.expunge(item)
        return res


async def update_doc_status(source: str, uri_hashes: dict[str, str]):
    status, to_delete = await get_doc_status(source, uri_hashes)
    logger.info(
        f" found {len(to_delete)} to delete for {source}",
        extra=log_context(action="update_doc_status", source=source),
    )
    async with models.get_session() as session:
        for row in to_delete:
            logger.info(
                f"deleting {row['uri']}",
                extra=log_context(action="update_doc_status", source=source),
            )
            uri = await find_document_uri(row["uri"], source)
            if uri:
                await delete_document_uri(uri.id, session)
    return status


async def get_uris_for_source(source: str) -> list[models.DocumentURI]:
    logger.debug(
        f"get history for {source}",
        extra=log_context(action="get_uris_for_source", source=source),
    )
    async with models.get_session() as session:
        q = select(models.DocumentURI).where(models.DocumentURI.source == source)
        rs = await session.exec(q)
        res = rs.all()
        for item in res:
            session.expunge(item)
        return res


async def get_doc_status(source: str, source_hashes: dict[str, str]):
    stored_uris = await get_uris_for_source(source)
    stored_dict = {x.uri: x.doc_hash for x in stored_uris}
    to_remove = []
    res = {}
    for source_uri, source_hash in source_hashes.items():
        if source_uri in stored_dict:
            extracted_source_hash = _extract_hash_value(source_hash)
            stored_hash = _extract_hash_value(stored_dict[source_uri])
            if extracted_source_hash == stored_hash:
                res[source_uri] = {"hash": extracted_source_hash, "status": "matched"}
            else:
                res[source_uri] = {
                    "source_hash": extracted_source_hash,
                    "stored_hash": stored_hash,
                    "status": "mismatch",
                }
            del stored_dict[source_uri]
        else:
            res[source_uri] = {"hash": source_hash, "status": "new"}
    for uri, doc_hash in stored_dict.items():
        if uri not in source_hashes:
            to_remove.append({"uri": uri, "hash": doc_hash, "status": "deleted"})
    return res, to_remove


async def get_uris_for_batch(batch_id: int) -> list[models.DocumentURI]:
    async with models.get_session() as session:
        q = select(models.DocumentURI).where(models.DocumentURI.batch_id == batch_id)
        rs = await session.exec(q)
        res = rs.all()
        for item in res:
            session.expunge(item)
        return res


async def new_batch(source: str, name: str = None) -> int:
    async with models.get_session() as session:
        batch = models.DocumentBatch(source=source, name=name, start_date=datetime.datetime.now(datetime.UTC))
        session.add(batch)
        await session.flush()
        await session.refresh(batch)
        batch_id = batch.id
        await session.commit()

        return batch_id


async def list_batches() -> list[models.DocumentBatch]:
    async with models.get_session() as session:
        q = select(models.DocumentBatch)
        rs = await session.exec(q)
        res = rs.all()
        for item in res:
            session.expunge(item)
        return res


async def get_batch(id: int) -> models.DocumentBatch | None:
    async with models.get_session() as session:
        q = select(models.DocumentBatch).where(models.DocumentBatch.id == id)
        rs = await session.exec(q)
        res = rs.first()
        if res:
            session.expunge(res)
            return res
        return None


async def get_documents_in_batch(batch_id: int) -> list[models.Document]:
    """
    Get all documents associated with a batch via DocumentURI.

    Uses a subquery to find documents whose hash exists in the documenturi
    table for the given batch_id.
    """
    async with models.get_session() as session:
        # Build subquery to get document hashes for this batch
        subquery = select(models.DocumentURI.doc_hash).where(models.DocumentURI.batch_id == batch_id)

        # Select documents where hash is in the subquery results
        q = select(models.Document).where(models.Document.hash.in_(subquery))

        rs = await session.exec(q)
        res = rs.all()

        # Expunge objects so they can be used outside the session
        for doc in res:
            session.expunge(doc)

        return res


async def delete_document(doc_hash: str, session, raise_on_error=True) -> models.Document:
    logger.debug(f"remove document {doc_hash}", extra=log_context(doc_hash=doc_hash))
    # make sure there aren't any uris pointing to it
    # not all backends actually enforce this
    u1 = select(models.DocumentURI).where(models.DocumentURI.doc_hash == doc_hash)
    urs = await session.exec(u1)
    found = urs.all()
    if found and len(found) != 0:
        if raise_on_error:
            raise ValueError(f"document {doc_hash} has existing uris. found {len(found)} ")
        else:
            logger.info(
                f"ignoring delete of document with existing uris {doc_hash}",
                extra=log_context(doc_hash=doc_hash),
            )
            return

    q = select(models.Document).where(models.Document.hash == doc_hash)
    rs = await session.exec(q)
    res = rs.first()
    if res:
        await session.delete(res)
        await session.flush()
        await delete_file(res.hash, session)
        # don't commit here  we're borowing the session from the caller
        # await session.commit()

    return res


async def delete_document_uri(doc_uri_id: int, session) -> models.DocumentURI:
    logger.debug(f"remove document uri {doc_uri_id}")
    q = select(models.DocumentURI).where(models.DocumentURI.id == doc_uri_id)
    rs = await session.exec(q)
    res = rs.first()
    if res:
        await session.delete(res)
        await delete_document(res.doc_hash, session, raise_on_error=False)
        await session.flush()
    else:
        raise DocumentNotFoundError(doc_uri_id)
    return res


async def get_document(doc_hash: str) -> models.Document:
    logger.debug(f"get document {doc_hash}", extra=log_context(doc_hash=doc_hash))
    async with models.get_session() as session:
        q = select(models.Document).where(models.Document.hash == doc_hash)
        rs = await session.exec(q)
        res = rs.first()
        if res:
            session.expunge(res)
            return res
        else:
            raise DocumentNotFoundError(doc_hash)


async def delete_orphaned_documents():
    """
    Delete orphaned documents that have no URI pointing to them.

    Also deletes orphaned history records, HaikuRAG entries, and DocumentDB
    records for consistency. Uses SQLModel ORM for cross-database compatibility.

    Returns
    -------
    dict
        Statistics about deleted records including:
        - deleted_documents: Number of Document records deleted
        - deleted_history: Number of DocumentURIHistory records deleted
        - deleted_rag_entries: Number of HaikuRAG documents deleted
        - deleted_documentdb_records: Number of DocumentDB records deleted
    """
    deleted_rag_entries = 0
    deleted_documentdb_records = 0

    async with models.get_session() as session:
        # Subquery: Get all hashes that are referenced
        referenced_hashes_subq = select(models.DocumentURI.doc_hash).distinct().subquery()

        # Find orphaned document hashes first (before deletion)
        orphaned_q = select(models.Document.hash).where(
            models.Document.hash.not_in(select(referenced_hashes_subq.c.doc_hash))
        )
        orphaned_result = await session.exec(orphaned_q)
        orphaned_hashes = list(orphaned_result.all())

    # Delete from HaikuRAG for each orphaned hash (outside the session to avoid conflicts)
    for doc_hash in orphaned_hashes:
        rag_stats = await rag.delete_from_rag_by_hash(doc_hash)
        deleted_rag_entries += rag_stats["deleted_rag_entries"]
        deleted_documentdb_records += rag_stats["deleted_documentdb_records"]

    async with models.get_session() as session:
        # Subquery: Get all hashes that are referenced (re-create for the new session)
        referenced_hashes_subq = select(models.DocumentURI.doc_hash).distinct().subquery()

        # Delete orphaned documents
        q1 = delete(models.Document).where(models.Document.hash.not_in(select(referenced_hashes_subq.c.doc_hash)))
        result1 = await session.exec(q1)
        deleted_docs = result1.rowcount  # type: ignore

        # Delete orphaned history
        q2 = delete(models.DocumentURIHistory).where(
            models.DocumentURIHistory.hash.not_in(select(referenced_hashes_subq.c.doc_hash))
        )
        result2 = await session.exec(q2)
        deleted_history = result2.rowcount  # type: ignore

        await session.commit()

        logger.info(
            f"Deleted {deleted_docs} orphaned documents, {deleted_history} history records, "
            f"{deleted_rag_entries} RAG entries, {deleted_documentdb_records} DocumentDB records"
        )

        return {
            "deleted_documents": deleted_docs,
            "deleted_history": deleted_history,
            "deleted_rag_entries": deleted_rag_entries,
            "deleted_documentdb_records": deleted_documentdb_records,
        }


async def validate_storage() -> dict[tuple[models.ArtifactType, models.ArtifactType], set[str]]:
    """Validate storage consistency across artifact types.

    Returns a dict mapping (artifact_type_1, artifact_type_2) to files present in
    artifact_type_1 but missing from artifact_type_2.
    """
    filesets: dict[models.ArtifactType, set[str]] = {}
    errors: dict[models.ArtifactType, str] = {}

    for st in models.ArtifactType:
        try:
            op = dal.get_storage_operator(st)
            files = await op.list("/")
            filesets[st] = set(files)
        except Exception as e:
            logger.warning(
                f"Failed to list files for storage type {st}: {e}",
                extra=log_context(action="validate_storage"),
            )
            errors[st] = str(e)
            filesets[st] = set()

    if errors:
        logger.warning(
            f"Storage validation completed with errors for {len(errors)} storage types",
            extra=log_context(action="validate_storage"),
        )

    diffs: dict[tuple[models.ArtifactType, models.ArtifactType], set[str]] = {}
    for s1 in models.ArtifactType:
        for s2 in models.ArtifactType:
            if s1 == s2:
                continue
            diff = filesets[s1] - filesets[s2]
            diffs[(s1, s2)] = diff

    return diffs


class DocumentURINotFoundError(ValueError):
    def __init__(self, uri: str, source: str):
        super().__init__(f"DocumentURI not found for uri={uri}, source={source}")


async def delete_document_uri_by_uri(uri: str, source: str) -> dict[str, int]:
    """
    Delete a DocumentURI by URI and source with cascading deletion.

    If only one DocumentURI references the underlying document, all associated
    records are deleted including workflow runs, steps, lifecycle history,
    artifacts, and the document itself.

    If multiple DocumentURIs reference the same document, only the specified
    DocumentURI and its history are deleted; the document is preserved.

    Parameters
    ----------
    uri : str
        The document URI to delete
    source : str
        The source system identifier

    Returns
    -------
    dict[str, int]
        A dictionary containing deletion statistics:
        - deleted_document_uris: Number of DocumentURI records deleted (1)
        - deleted_uri_history: Number of DocumentURIHistory records deleted
        - deleted_documents: Number of Document records deleted (0 or 1)
        - deleted_workflow_runs: Number of WorkflowRun records deleted
        - deleted_run_steps: Number of RunStep records deleted
        - deleted_lifecycle_history: Number of LifecycleHistory records deleted
        - total_deleted: Total number of records deleted

    Raises
    ------
    DocumentURINotFoundError
        If the DocumentURI with the specified uri and source does not exist
    """
    async with models.get_session() as session:
        # Step 1: Find the DocumentURI
        q = select(models.DocumentURI).where(models.DocumentURI.uri == uri).where(models.DocumentURI.source == source)
        result = await session.exec(q)
        doc_uri = result.first()

        if not doc_uri:
            raise DocumentURINotFoundError(uri, source)

        doc_hash = doc_uri.doc_hash
        doc_uri_id = doc_uri.id

        # Step 2: Check how many URIs reference this document
        uri_count_q = select(models.DocumentURI).where(models.DocumentURI.doc_hash == doc_hash)
        uri_count_result = await session.exec(uri_count_q)
        uri_count = len(uri_count_result.all())

        # Initialize counters
        deleted_documents = 0
        deleted_workflow_runs = 0
        deleted_run_steps = 0
        deleted_lifecycle_history = 0
        deleted_rag_entries = 0
        deleted_documentdb_records = 0

        # Step 3: If this is the only URI, cascade delete everything
        if uri_count == 1:
            # Get all workflow run IDs for this document
            workflow_run_q = select(models.WorkflowRun.id).where(models.WorkflowRun.doc_id == doc_hash)
            workflow_run_ids_result = await session.exec(workflow_run_q)
            workflow_run_ids = list(workflow_run_ids_result.all())

            # Delete RunSteps for all WorkflowRuns
            if workflow_run_ids:
                runstep_delete_q = delete(models.RunStep).where(models.RunStep.workflow_run_id.in_(workflow_run_ids))
                runstep_result = await session.exec(runstep_delete_q)
                deleted_run_steps = runstep_result.rowcount  # type: ignore

                # Delete LifecycleHistory for all WorkflowRuns
                lifecycle_delete_q = delete(models.LifecycleHistory).where(
                    models.LifecycleHistory.workflow_run_id.in_(workflow_run_ids)
                )
                lifecycle_result = await session.exec(lifecycle_delete_q)
                deleted_lifecycle_history = lifecycle_result.rowcount  # type: ignore

            # Delete WorkflowRuns
            workflowrun_delete_q = delete(models.WorkflowRun).where(models.WorkflowRun.doc_id == doc_hash)
            workflowrun_result = await session.exec(workflowrun_delete_q)
            deleted_workflow_runs = workflowrun_result.rowcount  # type: ignore

            # Delete file artifacts
            for artifact_type in models.ArtifactType:
                try:
                    op = dal.get_storage_operator(artifact_type)
                    await op.delete(doc_hash)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    logger.debug(
                        f"Could not delete artifact {artifact_type} for {doc_hash}: {e}",
                        extra=log_context(doc_hash=doc_hash, action="delete_document_uri_by_uri"),
                    )

            # Delete from HaikuRAG and DocumentDB
            rag_delete_stats = await rag.delete_from_rag_by_hash(doc_hash)
            deleted_rag_entries = rag_delete_stats["deleted_rag_entries"]
            deleted_documentdb_records = rag_delete_stats["deleted_documentdb_records"]

            # Delete Document
            doc_delete_q = delete(models.Document).where(models.Document.hash == doc_hash)
            doc_result = await session.exec(doc_delete_q)
            deleted_documents = doc_result.rowcount  # type: ignore

        # Step 4: Delete DocumentURIHistory for this URI
        history_delete_q = delete(models.DocumentURIHistory).where(models.DocumentURIHistory.doc_uri_id == doc_uri_id)
        history_result = await session.exec(history_delete_q)
        deleted_uri_history = history_result.rowcount  # type: ignore

        # Step 5: Delete the DocumentURI
        uri_delete_q = delete(models.DocumentURI).where(models.DocumentURI.id == doc_uri_id)
        await session.exec(uri_delete_q)
        deleted_document_uris = 1

        await session.commit()

        return {
            "deleted_document_uris": deleted_document_uris,
            "deleted_uri_history": deleted_uri_history,
            "deleted_documents": deleted_documents,
            "deleted_workflow_runs": deleted_workflow_runs,
            "deleted_run_steps": deleted_run_steps,
            "deleted_lifecycle_history": deleted_lifecycle_history,
            "deleted_rag_entries": deleted_rag_entries,
            "deleted_documentdb_records": deleted_documentdb_records,
            "total_deleted": (
                deleted_document_uris
                + deleted_uri_history
                + deleted_documents
                + deleted_workflow_runs
                + deleted_run_steps
                + deleted_lifecycle_history
                + deleted_rag_entries
                + deleted_documentdb_records
            ),
        }


async def list_documentdb_databases() -> list[dict]:
    """
    List distinct lancedb_dir/db_name combinations from DocumentDB with document counts.

    Returns
    -------
    list[dict]
        List of dictionaries containing:
        - lancedb_dir: The LanceDB directory path
        - db_name: The database name (data_dir)
        - document_count: Number of documents in this database
        - total_chunks: Total chunks across all documents
    """
    from sqlalchemy import func

    async with models.get_session() as session:
        # Query distinct lancedb_dir/db_name with counts
        q = (
            select(
                models.DocumentDB.lancedb_dir,
                models.DocumentDB.db_name,
                func.count(models.DocumentDB.id).label("document_count"),
                func.sum(models.DocumentDB.chunk_count).label("total_chunks"),
            )
            .group_by(models.DocumentDB.lancedb_dir, models.DocumentDB.db_name)
            .order_by(models.DocumentDB.lancedb_dir, models.DocumentDB.db_name)
        )

        result = await session.exec(q)
        rows = result.all()

        databases = []
        for row in rows:
            databases.append(
                {
                    "lancedb_dir": row.lancedb_dir,
                    "db_name": row.db_name,
                    "document_count": row.document_count,
                    "total_chunks": row.total_chunks or 0,
                }
            )

        return databases


async def list_documents_in_rag_db(
    db_name: str,
    lancedb_dir: str | None = None,
) -> list[dict]:
    """
    List documents in a specific RAG database from DocumentDB records.

    Joins DocumentDB with DocumentURI and Document to provide full document information.

    Parameters
    ----------
    db_name : str
        The database name (data_dir) to filter by
    lancedb_dir : str | None
        The LanceDB directory path. If None, uses default from settings.

    Returns
    -------
    list[dict]
        List of dictionaries containing document information:
        - doc_hash: The document hash
        - rag_id: The HaikuRAG document ID
        - chunk_count: Number of chunks for this document
        - created_date: When the document was added to RAG
        - uri: The document URI (from DocumentURI)
        - source: The source system
        - mime_type: Document MIME type
        - file_size: Document file size in bytes
    """
    if lancedb_dir is None:
        lancedb_dir = get_settings().lancedb_dir

    async with models.get_session() as session:
        # Query DocumentDB joined with DocumentURI and Document
        q = (
            select(
                models.DocumentDB.doc_hash,
                models.DocumentDB.rag_id,
                models.DocumentDB.chunk_count,
                models.DocumentDB.created_date,
                models.DocumentDB.source,
                models.DocumentURI.uri,
                models.Document.mime_type,
                models.Document.file_size,
            )
            .join(models.DocumentURI, models.DocumentDB.doc_hash == models.DocumentURI.doc_hash, isouter=True)
            .join(models.Document, models.DocumentDB.doc_hash == models.Document.hash, isouter=True)
            .where(models.DocumentDB.db_name == db_name)
            .where(models.DocumentDB.lancedb_dir == lancedb_dir)
            .order_by(models.DocumentDB.created_date.desc())
        )

        result = await session.exec(q)
        rows = result.all()

        documents = []
        for row in rows:
            documents.append(
                {
                    "doc_hash": row.doc_hash,
                    "rag_id": row.rag_id,
                    "chunk_count": row.chunk_count or 0,
                    "created_date": row.created_date.isoformat() if row.created_date else None,
                    "source": row.source,
                    "uri": row.uri,
                    "mime_type": row.mime_type,
                    "file_size": row.file_size,
                }
            )

        return documents


async def check_rag_db_consistency(
    db_name: str,
    lancedb_dir: str | None = None,
) -> dict:
    """
    Check consistency between LanceDB database and DocumentDB records.

    Compares documents in the actual LanceDB database with records tracked
    in DocumentDB to find discrepancies.

    Parameters
    ----------
    db_name : str
        The database name (data_dir) to check
    lancedb_dir : str | None
        The LanceDB directory path. If None, uses default from settings.

    Returns
    -------
    dict
        Dictionary containing:
        - db_name: The database name checked
        - lancedb_dir: The LanceDB directory path
        - db_path: Full path to the LanceDB database
        - in_documentdb_only: List of documents in DocumentDB but not in LanceDB
        - in_lancedb_only: List of documents in LanceDB but not in DocumentDB
        - matched: Number of documents that exist in both
        - documentdb_count: Total documents in DocumentDB for this database
        - lancedb_count: Total documents in LanceDB database
    """
    from pathlib import Path

    from haiku.rag.client import HaikuRAG
    from haiku.rag.config import get_config

    settings = get_settings()
    if lancedb_dir is None:
        lancedb_dir = settings.lancedb_dir

    # Build db_path
    if lancedb_dir.startswith("s3://"):
        if lancedb_dir.endswith("/"):
            db_path = f"{lancedb_dir}{db_name}"
        else:
            db_path = f"{lancedb_dir}/{db_name}"
    else:
        db_path = Path(lancedb_dir) / db_name

    # Get documents from DocumentDB
    documentdb_docs = await list_documents_in_rag_db(db_name, lancedb_dir)
    documentdb_rag_ids = {doc["rag_id"] for doc in documentdb_docs if doc["rag_id"]}

    # Get documents from LanceDB
    lancedb_docs = []
    lancedb_rag_ids = set()

    try:
        config = get_config()
        async with HaikuRAG(
            db_path=db_path,
            config=config,
            read_only=True,
        ) as client:
            documents = await client.list_documents()
            for doc in documents:
                doc_info = {
                    "rag_id": doc.id,
                    "uri": doc.uri,
                    "title": getattr(doc, "title", None),
                    "created_at": doc.created_at.isoformat() if getattr(doc, "created_at", None) else None,
                    "chunk_count": getattr(doc, "chunk_count", None),
                    "metadata": getattr(doc, "metadata", {}),
                }
                lancedb_docs.append(doc_info)
                if doc.id:
                    lancedb_rag_ids.add(doc.id)
    except Exception as e:
        # Database might not exist or be accessible
        return {
            "db_name": db_name,
            "lancedb_dir": lancedb_dir,
            "db_path": str(db_path),
            "error": f"Could not access LanceDB database: {e}",
            "in_documentdb_only": [
                {
                    "rag_id": doc["rag_id"],
                    "doc_hash": doc["doc_hash"],
                    "uri": doc["uri"],
                    "source": doc["source"],
                }
                for doc in documentdb_docs
            ],
            "in_lancedb_only": [],
            "matched": 0,
            "documentdb_count": len(documentdb_docs),
            "lancedb_count": 0,
        }

    # Find discrepancies
    in_documentdb_only_ids = documentdb_rag_ids - lancedb_rag_ids
    in_lancedb_only_ids = lancedb_rag_ids - documentdb_rag_ids
    matched_ids = documentdb_rag_ids & lancedb_rag_ids

    # Build detailed lists
    in_documentdb_only = [
        {
            "rag_id": doc["rag_id"],
            "doc_hash": doc["doc_hash"],
            "uri": doc["uri"],
            "source": doc["source"],
        }
        for doc in documentdb_docs
        if doc["rag_id"] in in_documentdb_only_ids
    ]

    lancedb_by_rag_id = {doc["rag_id"]: doc for doc in lancedb_docs}
    in_lancedb_only = [
        {
            "rag_id": rag_id,
            "uri": lancedb_by_rag_id[rag_id].get("uri"),
            "title": lancedb_by_rag_id[rag_id].get("title"),
            "metadata": lancedb_by_rag_id[rag_id].get("metadata"),
        }
        for rag_id in in_lancedb_only_ids
    ]

    return {
        "db_name": db_name,
        "lancedb_dir": lancedb_dir,
        "db_path": str(db_path),
        "in_documentdb_only": in_documentdb_only,
        "in_lancedb_only": in_lancedb_only,
        "matched": len(matched_ids),
        "documentdb_count": len(documentdb_docs),
        "lancedb_count": len(lancedb_docs),
    }
