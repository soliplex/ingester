import logging
import pathlib
from typing import Protocol
from typing import runtime_checkable

import aiofiles
import opendal
from aiofiles import os as aos
from fsspec.core import url_to_fs
from sqlalchemy import func
from sqlmodel import select

from . import models
from .config import S3Settings
from .config import get_settings

logger = logging.getLogger(__name__)


@runtime_checkable
class StorageOperator(Protocol):
    """Protocol defining the interface for storage backends.

    All storage operators (DB, filesystem, S3) must implement this interface.
    This allows for consistent usage regardless of the underlying storage mechanism.
    """

    async def read(self, path: str) -> bytes:
        """Read data from the given path."""
        ...

    async def write(self, path: str, data: bytes) -> None:
        """Write data to the given path."""
        ...

    async def exists(self, path: str) -> bool:
        """Check if data exists at the given path."""
        ...

    async def delete(self, path: str) -> None:
        """Delete data at the given path."""
        ...

    async def list(self, prefix: str) -> list[str]:
        """List all keys with the given prefix."""
        ...

    def get_uri(self, path: str) -> str:
        """Get a URI representation for the given path."""
        ...


async def recursive_listdir(file_dir: pathlib.Path):
    file_paths = []
    ls = await aos.listdir(file_dir)
    for entry in ls:
        ed = file_dir / entry
        isdir = await aos.path.isdir(ed)
        if isdir:
            ext = await recursive_listdir(ed)
            file_paths.extend(ext)
        else:
            file_paths.append(ed)
    return file_paths


async def read_input_url(input_url: str) -> bytes:
    """Read a file from a URL (file:// or s3://).

    Args:
        input_url: URL to read from, either file:// or s3:// scheme.

    Returns:
        The file contents as bytes.

    Raises:
        ValueError: If the URL scheme is not supported.
    """
    if input_url.startswith("file://"):
        return await read_file_url(input_url)
    elif input_url.startswith("s3://"):
        return await read_s3_url(input_url)
    else:
        raise ValueError(f"Unknown uri scheme: {input_url}")


async def read_file_url(input_url: str):
    """
    read a file from file uri as an input to ingester
    """
    _, local_path = url_to_fs(input_url)
    async with aiofiles.open(local_path, "rb") as f:
        return await f.read()


def validate_s3_settings(s3: S3Settings):
    if not s3.access_key_id or s3.access_key_id == "default":
        raise ValueError("s3.access_key_id is required")
    if not s3.access_secret or s3.access_secret == "default":
        raise ValueError("s3.access_secret is required")
    if not s3.region or s3.region == "default":
        raise ValueError("s3.region is required")
    if not s3.bucket or s3.bucket == "default":
        raise ValueError("s3.bucket is required")


def create_s3_operator(s3: S3Settings, root: str = None) -> opendal.AsyncOperator:
    validate_s3_settings(s3)
    return opendal.AsyncOperator(
        "s3",
        bucket=s3.bucket,
        endpoint=s3.endpoint_url,
        access_key_id=s3.access_key_id,
        secret_access_key=s3.access_secret,
        region=s3.region,
        root=root,
    )


async def read_s3_url(input_url: str):
    """Read a file from S3 as an input to ingester.

    Args:
        input_url: S3 URL in the format s3://bucket/key/path

    Returns:
        The file contents as bytes.

    Raises:
        ValueError: If the bucket doesn't match the configured input_s3 bucket.
    """
    from urllib.parse import urlparse

    parsed = urlparse(input_url)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    logger.info(f"reading s3 bucket={bucket} key={key}")
    settings = get_settings()
    if bucket != settings.input_s3.bucket:
        raise ValueError(f"bucket {bucket} does not match configured bucket {settings.input_s3.bucket}")
    op = create_s3_operator(settings.input_s3)
    return await op.read(key)


class DBStorageOperator:
    """Database-backed storage operator.

    Stores binary data in a database table, using hash as the key.
    Implements the StorageOperator protocol.
    """

    def __init__(self, artifact_type: str, storage_root: str):
        self.artifact_type = artifact_type
        self.storage_root = storage_root

    async def read(self, path: str) -> bytes:
        async with models.get_session() as session:
            rs = await session.exec(
                select(models.DocumentBytes)
                .where(models.DocumentBytes.hash == path)
                .where(models.DocumentBytes.artifact_type == self.artifact_type)
                .where(models.DocumentBytes.storage_root == self.storage_root)
            )
            res = rs.first()
            if res:
                return res.file_bytes
            else:
                raise FileNotFoundError(path)

    async def exists(self, path: str) -> bool:
        async with models.get_session() as session:
            statement = (
                select(func.count())
                .select_from(models.DocumentBytes)
                .where(models.DocumentBytes.hash == path)
                .where(models.DocumentBytes.artifact_type == self.artifact_type)
                .where(models.DocumentBytes.storage_root == self.storage_root)
            )
            rs = await session.exec(statement)
            ct = rs.first()
            logger.debug(f"exists found {ct} for {path}")
            return ct > 0

    async def write(self, path: str, data: bytes) -> None:
        async with models.get_session() as session:
            docbytes = models.DocumentBytes(
                hash=path,
                file_size=len(data),
                file_bytes=data,
                artifact_type=self.artifact_type,
                storage_root=self.storage_root,
            )
            session.add(docbytes)
            await session.commit()

    async def list(self, prefix: str) -> list[str]:
        async with models.get_session() as session:
            rs = await session.exec(
                select(models.DocumentBytes)
                .where(models.DocumentBytes.artifact_type == self.artifact_type)
                .where(models.DocumentBytes.storage_root == self.storage_root)
            )
            res = rs.all()
            return [r.hash for r in res]

    def get_uri(self, path: str) -> str:
        return f"bytes://{path}"

    async def delete(self, path: str) -> None:
        async with models.get_session() as session:
            rs = await session.exec(
                select(models.DocumentBytes)
                .where(models.DocumentBytes.hash == path)
                .where(models.DocumentBytes.artifact_type == self.artifact_type)
                .where(models.DocumentBytes.storage_root == self.storage_root)
            )
            res = rs.first()
            if res:
                await session.delete(res)
                await session.commit()
            else:
                raise FileNotFoundError(path)


class FileStorageOperator:
    """Filesystem-backed storage operator with hash-based sharding.

    Stores files in a directory structure using the last 2 characters
    of the path/hash as a subdirectory for better filesystem performance.
    Implements the StorageOperator protocol.
    """

    SHARD_SUFFIX_LENGTH = 2  # Use last N chars of path for subdirectory sharding

    def __init__(self, store_path: str):
        path = pathlib.Path(store_path)
        if not path.is_absolute():
            path = pathlib.Path.cwd() / store_path
        self.store_path = str(path)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)

    def _get_normalized_path(self, path: str) -> pathlib.Path:
        """Get the full filesystem path for a given key, creating shard directory if needed."""
        subdir = path[-self.SHARD_SUFFIX_LENGTH :]
        shard_dir = pathlib.Path(self.store_path) / subdir
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / path

    async def read(self, path: str) -> bytes:
        norm_path = self._get_normalized_path(path)
        async with aiofiles.open(norm_path, "rb") as f:
            return await f.read()

    async def exists(self, path: str) -> bool:
        norm_path = self._get_normalized_path(path)
        return await aos.path.exists(norm_path)

    async def write(self, path: str, data: bytes) -> None:
        norm_path = self._get_normalized_path(path)
        async with aiofiles.open(norm_path, "wb") as f:
            await f.write(data)

    async def delete(self, path: str) -> None:
        norm_path = self._get_normalized_path(path)
        await aos.unlink(norm_path)

    async def list(self, prefix: str) -> list[str]:
        base_path = pathlib.Path(self.store_path)
        files = await recursive_listdir(base_path)
        return [f.name for f in files]

    def get_uri(self, path: str) -> str:
        norm_path = self._get_normalized_path(path)
        return norm_path.as_uri()


class OpenDALAdapter:
    """Adapter wrapping opendal.AsyncOperator to conform to StorageOperator protocol.

    This allows OpenDAL's S3 operator (and other OpenDAL operators) to be used
    interchangeably with DBStorageOperator and FileStorageOperator.
    """

    def __init__(self, op: opendal.AsyncOperator, root: str = ""):
        self._op = op
        self._root = root

    async def read(self, path: str) -> bytes:
        return await self._op.read(path)

    async def write(self, path: str, data: bytes) -> None:
        await self._op.write(path, data)

    async def exists(self, path: str) -> bool:
        return await self._op.exists(path)

    async def delete(self, path: str) -> None:
        await self._op.delete(path)

    async def list(self, prefix: str) -> list[str]:
        entries = []
        async for entry in await self._op.list(prefix):
            entries.append(entry.path)
        return entries

    def get_uri(self, path: str) -> str:
        return f"s3://{self._root}/{path}" if self._root else f"s3://{path}"


def get_storage_operator(
    artifact_type: models.ArtifactType,
    step_config: models.StepConfig | None = None,
) -> StorageOperator:
    """Get a storage operator for the given artifact type.

    Args:
        artifact_type: The type of artifact to store/retrieve.
        step_config: Configuration for the processing step (required for non-DOC artifacts).

    Returns:
        A StorageOperator implementation appropriate for the configured storage target.

    Raises:
        ValueError: If artifact_type doesn't match step_config, or if step_config
            is required but not provided, or if the storage target is unknown.
    """
    if step_config is not None:
        expected_artifact_type = models.ARTIFACTS_FROM_STEPS[step_config.step_type]
        if artifact_type not in expected_artifact_type:
            raise ValueError(f"Artifact type {artifact_type} is not expected for step type {step_config.step_type}")
    if step_config is None and artifact_type != models.ArtifactType.DOC:
        raise ValueError("step_config is required for non-document artifacts")

    settings = get_settings()
    target = settings.file_store_target
    st = artifact_type.value

    if artifact_type == models.ArtifactType.DOC:
        root = ""
    else:
        root = str(step_config.id)

    if target == "s3":
        raw_op = create_s3_operator(settings.artifact_s3, root)
        return OpenDALAdapter(raw_op, root)
    elif target == "fs":
        fs_root = f"{settings.file_store_dir}/{getattr(settings, f'{st}_store_dir')}/{root}"
        return FileStorageOperator(fs_root)
    elif target == "db":
        return DBStorageOperator(st, root)
    else:
        raise ValueError(f"Unknown target {target}")
