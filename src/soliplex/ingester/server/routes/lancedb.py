"""LanceDB database management routes."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Response
from fastapi import status
from haiku.rag.app import HaikuRAGApp
from haiku.rag.config import get_config
from soliplex.ingester.lib.auth import get_current_user

from soliplex.ingester.lib.config import get_settings

logger = logging.getLogger(__name__)

lancedb_router = APIRouter(
    prefix="/api/v1/lancedb",
    tags=["lancedb"],
    dependencies=[Depends(get_current_user)],
)


def create_app(db: Path | None = None, read_only: bool = False) -> HaikuRAGApp:
    """Create HaikuRAGApp with loaded config and resolved database path.

    Args:
        db: Optional database path. If None, uses path from config.

    Returns:
        HaikuRAGApp instance with proper config and db path.
    """
    config = get_config()
    db_path = db if db else config.storage.data_dir / "haiku.rag.lancedb"
    return HaikuRAGApp(
        db_path=db_path,
        config=config,
        read_only=read_only,
    )


def get_folder_size(path: Path) -> int:
    """Calculate total size of a folder in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except (OSError, PermissionError):
        pass
    return total


def format_bytes(size: int) -> str:
    """Format bytes to human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def resolve_lancedb_path(db_name: str, lancedb_dir: str) -> Path:
    """
    Resolve the lancedb path from a db name.

    Appends '/haiku.rag.lancedb' if not already present.
    """
    db_path = Path(lancedb_dir) / db_name
    if not db_name.endswith(".lancedb"):
        db_path = db_path / "haiku.rag.lancedb"
    return db_path


@lancedb_router.get("/list", status_code=status.HTTP_200_OK, summary="List all LanceDB databases")
async def list_databases():
    """
    List all folders in the lancedb_dir.

    Returns each folder's name and storage size.
    """
    settings = get_settings()
    lancedb_dir = Path(settings.lancedb_dir)

    if not lancedb_dir.exists():
        return {
            "status": "ok",
            "lancedb_dir": str(lancedb_dir),
            "databases": [],
            "message": "LanceDB directory does not exist",
        }

    databases = []
    try:
        for entry in sorted(lancedb_dir.iterdir()):
            if entry.is_dir():
                size_bytes = get_folder_size(entry)
                databases.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "size_bytes": size_bytes,
                        "size_human": format_bytes(size_bytes),
                    }
                )
    except (OSError, PermissionError) as e:
        logger.warning(f"Error listing lancedb directory: {e}")

    return {
        "status": "ok",
        "lancedb_dir": str(lancedb_dir),
        "database_count": len(databases),
        "databases": databases,
    }


@lancedb_router.get("/info", status_code=status.HTTP_200_OK, summary="Get LanceDB database info")
async def get_info(
    response: Response,
    db: str = Query(..., description="Database name relative to lancedb_dir"),
):
    """
    Get information about a specific LanceDB database.

    Equivalent to 'haiku-rag info' CLI command.

    The db parameter is relative to lancedb_dir and will have
    '/haiku.rag.lancedb' appended if not already present.
    """
    from importlib.metadata import version as pkg_version

    import lancedb

    settings = get_settings()
    db_path = resolve_lancedb_path(db, settings.lancedb_dir)

    if not db_path.exists():
        response.status_code = status.HTTP_404_NOT_FOUND
        return {
            "status": "error",
            "error": f"Database not found: {db_path}",
        }

    # Get version info
    try:
        ldb_version = pkg_version("lancedb")
    except Exception:
        ldb_version = "unknown"
    try:
        hr_version = pkg_version("haiku.rag-slim")
    except Exception:
        hr_version = "unknown"

    # Connect to database
    try:
        db_conn = lancedb.connect(db_path)
        table_names = set(db_conn.table_names())
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {
            "status": "error",
            "error": f"Failed to open database: {e}",
        }

    # Get table statistics using haiku.rag Store
    try:
        from haiku.rag.config import get_config
        from haiku.rag.store.engine import Store

        config = get_config()
        store = Store(db_path, config=config, skip_validation=True)
        table_stats = store.get_stats()
        store.close()
    except Exception as e:
        logger.warning(f"Could not get table stats via Store: {e}")
        table_stats = {
            "documents": {"num_rows": 0, "total_bytes": 0},
            "chunks": {"num_rows": 0, "total_bytes": 0, "has_vector_index": False},
        }

    # Read settings from database
    stored_version = "unknown"
    embed_provider = None
    embed_model = None
    vector_dim = None

    if "settings" in table_names:
        try:
            settings_tbl = db_conn.open_table("settings")
            arrow = settings_tbl.search().where("id = 'settings'").limit(1).to_arrow()
            rows = arrow.to_pylist() if arrow is not None else []
            if rows:
                raw = rows[0].get("settings") or "{}"
                data = json.loads(raw) if isinstance(raw, str) else (raw or {})
                stored_version = str(data.get("version", stored_version))
                embeddings = data.get("embeddings", {})
                embed_model_obj = embeddings.get("model", {})
                embed_provider = embed_model_obj.get("provider")
                embed_model = embed_model_obj.get("name")
                vector_dim = embed_model_obj.get("vector_dim")
        except Exception as e:
            logger.warning(f"Could not read settings table: {e}")

    # Get table version counts
    doc_versions = 0
    chunk_versions = 0
    try:
        if "documents" in table_names:
            doc_versions = len(list(db_conn.open_table("documents").list_versions()))
        if "chunks" in table_names:
            chunk_versions = len(list(db_conn.open_table("chunks").list_versions()))
    except Exception:
        pass

    # Extract stats
    num_docs = table_stats["documents"].get("num_rows", 0)
    doc_bytes = table_stats["documents"].get("total_bytes", 0)
    num_chunks = table_stats["chunks"].get("num_rows", 0)
    chunk_bytes = table_stats["chunks"].get("total_bytes", 0)
    has_vector_index = table_stats["chunks"].get("has_vector_index", False)
    num_indexed_rows = table_stats["chunks"].get("num_indexed_rows", 0)
    num_unindexed_rows = table_stats["chunks"].get("num_unindexed_rows", 0)

    return {
        "status": "ok",
        "path": str(db_path),
        "versions": {
            "lancedb": ldb_version,
            "haiku_rag": hr_version,
            "stored_version": stored_version,
        },
        "embeddings": {
            "provider": embed_provider,
            "model": embed_model,
            "vector_dim": vector_dim,
        },
        "documents": {
            "count": num_docs,
            "size_bytes": doc_bytes,
            "size_human": format_bytes(doc_bytes),
            "versions": doc_versions,
        },
        "chunks": {
            "count": num_chunks,
            "size_bytes": chunk_bytes,
            "size_human": format_bytes(chunk_bytes),
            "versions": chunk_versions,
        },
        "vector_index": {
            "exists": has_vector_index,
            "indexed_rows": num_indexed_rows,
            "unindexed_rows": num_unindexed_rows,
        },
        "tables": list(table_names),
    }


@lancedb_router.get(
    "/vacuum", status_code=status.HTTP_200_OK, summary="Optimize and clean up all tables to reduce disk usage"
)
async def vacuum(response: Response, db: str = Query(..., description="Database name relative to lancedb_dir")):
    settings = get_settings()
    db_path = resolve_lancedb_path(db, settings.lancedb_dir)
    app = create_app(db_path)
    try:
        await app.vacuum()
    except Exception as e:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {
            "status": "error",
            "error": f"Failed to vacuum database: {e}",
        }
    else:
        return {"status": "ok"}


@lancedb_router.get("/documents", status_code=status.HTTP_200_OK, summary="List documents in LanceDB")
async def list_documents(
    response: Response,
    db: str = Query(..., description="Database name relative to lancedb_dir"),
    limit: int | None = Query(None, description="Maximum number of documents to return"),
    offset: int | None = Query(None, description="Number of documents to skip"),
    filter: str | None = Query(None, description="SQL WHERE clause to filter documents"),
):
    """
    List all documents in a LanceDB database.

    Equivalent to 'haiku-rag list' CLI command.

    The db parameter is relative to lancedb_dir and will have
    '/haiku.rag.lancedb' appended if not already present.
    """
    settings = get_settings()
    db_path = resolve_lancedb_path(db, settings.lancedb_dir)

    if not db_path.exists():
        response.status_code = status.HTTP_404_NOT_FOUND
        return {
            "status": "error",
            "error": f"Database not found: {db_path}",
        }

    try:
        from haiku.rag.client import HaikuRAG
        from haiku.rag.config import get_config

        config = get_config()

        async with HaikuRAG(
            db_path=db_path,
            config=config,
            read_only=True,
        ) as client:
            documents = await client.list_documents(
                limit=limit,
                offset=offset,
                filter=filter,
            )

            # Convert documents to serializable format
            doc_list = []
            for doc in documents:
                doc_dict = {
                    "id": doc.id,
                    "uri": doc.uri,
                    "title": getattr(doc, "title", None),
                    "created_at": doc.created_at.isoformat() if getattr(doc, "created_at", None) else None,
                    "updated_at": doc.updated_at.isoformat() if getattr(doc, "updated_at", None) else None,
                    "chunk_count": getattr(doc, "chunk_count", None),
                }
                # Include metadata if present
                if hasattr(doc, "metadata") and doc.metadata:
                    doc_dict["metadata"] = doc.metadata
                doc_list.append(doc_dict)

            return {
                "status": "ok",
                "path": str(db_path),
                "document_count": len(doc_list),
                "documents": doc_list,
            }

    except Exception as e:
        logger.exception("Error listing documents", exc_info=e)
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        return {
            "status": "error",
            "error": str(e),
        }
