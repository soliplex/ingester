import json
import logging

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import Response
from fastapi import UploadFile
from fastapi import status

from soliplex.ingester.lib import operations
from soliplex.ingester.lib import workflow as workflow
from soliplex.ingester.lib.auth import get_current_user

logger = logging.getLogger(__name__)

doc_router = APIRouter(prefix="/api/v1/document", tags=["document"], dependencies=[Depends(get_current_user)])


@doc_router.get("/", status_code=status.HTTP_200_OK)
async def get_docs(response: Response, source: str = None, batch_id: int = None):
    if source:
        docs = await operations.get_uris_for_source(source)
        return docs
    elif batch_id:
        docs = await operations.get_uris_for_batch(batch_id)
        return docs
    else:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {
            "error": "No source or batch_id provided",
            "status_code": status.HTTP_400_BAD_REQUEST,
        }


@doc_router.post("/ingest-document", status_code=status.HTTP_201_CREATED)
async def ingest_document(
    response: Response,
    file: UploadFile = None,
    input_uri: str = Form(None),
    mime_type: str = Form(None),
    source_uri: str = Form(...),
    source: str = Form(...),
    batch_id: int = Form(...),
    doc_meta: str = Form("{}"),
    priority: int = Form(0),
):
    logger.info(f"Received file: {file} from {source}")
    if file:
        file_bytes = await file.read()
    else:
        file_bytes = None
    try:
        meta_dict = json.loads(doc_meta)
    except json.JSONDecodeError:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": "metadata should be valid JSON object"}
    if not isinstance(meta_dict, dict):
        raise TypeError("Metadata must be a dictionary")  # noqa: TRY003
    meta_dict["batch_id"] = str(batch_id)
    try:
        docuri, doc = await workflow.initial_load(
            source_uri=source_uri,
            source=source,
            batch_id=batch_id,
            doc_meta=meta_dict,
            file_bytes=file_bytes,
            input_uri=input_uri,
            mime_type=mime_type,
        )
        if docuri.batch_id == batch_id:
            res = {
                "batch_id": docuri.batch_id,
                "document_uri": source_uri,
                "document_hash": doc.hash,
                "source": source,
                "uri_id": docuri.id,
            }
        else:
            response.status_code = status.HTTP_203_NON_AUTHORITATIVE_INFORMATION
            res = {
                "batch_id": docuri.batch_id,
                "document_uri": source_uri,
                "document_hash": doc.hash,
                "source": source,
                "uri_id": docuri.id,
            }

    except KeyError as e:
        logger.exception("Error ingesting document")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"error": str(e)}
    except Exception as ex:
        logger.exception("Error ingesting document")
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(ex)}
    else:
        return res


@doc_router.post(
    "/cleanup-orphans",
    status_code=status.HTTP_200_OK,
    summary="Delete orphaned documents with no URI references",
)
async def cleanup_orphaned_documents(response: Response):
    """
    Delete documents that have no associated DocumentURI records.

    This is a maintenance operation that removes:
    - Documents with no URI references
    - Associated DocumentURIHistory records for orphaned documents

    Returns:
        dict: Statistics about deleted records
            - deleted_documents: Number of documents deleted
            - deleted_history: Number of history records deleted
    """
    try:
        stats = await operations.delete_orphaned_documents()
    except Exception as e:
        logger.exception("Error cleaning up orphaned documents", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}
    else:
        return {"message": "Orphaned documents cleaned up", "statistics": stats}


@doc_router.delete(
    "/by-uri",
    status_code=status.HTTP_200_OK,
    summary="Delete a DocumentURI by URI and source with cascading deletion",
)
async def delete_document_by_uri(response: Response, uri: str, source: str) -> dict:
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
    dict
        Deletion statistics including counts of deleted records by type
    """
    try:
        result = await operations.delete_document_uri_by_uri(uri, source)
    except operations.DocumentURINotFoundError as e:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"error": str(e)}
    except Exception as e:
        logger.exception("Error deleting document URI", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {"error": str(e)}
    else:
        return {
            "message": "DocumentURI deleted successfully",
            "uri": uri,
            "source": source,
            "statistics": result,
        }
