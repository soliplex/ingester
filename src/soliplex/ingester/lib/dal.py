import asyncio
import base64
import errno
import hashlib
import hmac as hmac_mod
import logging
import pathlib
import shutil
from typing import Protocol
from typing import runtime_checkable

import aiofiles
import opendal
import zstandard
from aiofiles import os as aos
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fsspec.core import url_to_fs
from sqlalchemy import func
from sqlmodel import select

from . import models
from .config import ProtectionLevel
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


class IntegrityError(Exception):
    """Raised when file integrity verification fails (HASH or HMAC mismatch)."""


class ProtectedStorageOperator:
    """Decorator that adds integrity/confidentiality protection to a StorageOperator.

    Wraps an inner StorageOperator and applies the configured protection level
    transparently on write and read operations.
    """

    _PROTECTED_EXTENSIONS = (".hash", ".hmac", ".enc")

    def __init__(
        self,
        inner: StorageOperator,
        protection_level: ProtectionLevel,
        secret: str | None = None,
    ):
        self._inner = inner
        self._protection_level = protection_level
        self._hmac_key: bytes = b""
        self._fernet: Fernet | None = None
        if protection_level in (ProtectionLevel.HMAC, ProtectionLevel.ENCRYPT):
            if not secret:
                raise ValueError(f"secret required for {protection_level}")
            master_key = secret.encode("utf-8")
            self._hmac_key = _derive_key(master_key, b"hmac-sha512", 64)
            fernet_raw = _derive_key(master_key, b"fernet-v1", 32)
            self._fernet = Fernet(base64.urlsafe_b64encode(fernet_raw))

    async def write(self, path: str, data: bytes) -> None:
        match self._protection_level:
            case ProtectionLevel.HASH:
                digest = hashlib.sha512(data).hexdigest()
                await self._inner.write(path, data)
                await self._inner.write(f"{path}.hash", digest.encode())
            case ProtectionLevel.HMAC:
                digest = hmac_mod.new(self._hmac_key, data, hashlib.sha512).hexdigest()
                await self._inner.write(path, data)
                await self._inner.write(f"{path}.hmac", digest.encode())
            case ProtectionLevel.ENCRYPT:
                encrypted = self._fernet.encrypt(data)
                await self._inner.write(f"{path}.enc", encrypted)
            case _:
                await self._inner.write(path, data)

    async def read(self, path: str) -> bytes:
        match self._protection_level:
            case ProtectionLevel.HASH:
                data = await self._inner.read(path)
                stored_hash = (await self._inner.read(f"{path}.hash")).decode()
                computed = hashlib.sha512(data).hexdigest()
                if not hmac_mod.compare_digest(stored_hash, computed):
                    raise IntegrityError(f"SHA-512 hash mismatch for {path}")
                return data
            case ProtectionLevel.HMAC:
                data = await self._inner.read(path)
                stored_mac = (await self._inner.read(f"{path}.hmac")).decode()
                computed = hmac_mod.new(self._hmac_key, data, hashlib.sha512).hexdigest()
                if not hmac_mod.compare_digest(stored_mac, computed):
                    raise IntegrityError(f"HMAC-SHA-512 verification failed for {path}")
                return data
            case ProtectionLevel.ENCRYPT:
                encrypted = await self._inner.read(f"{path}.enc")
                return self._fernet.decrypt(encrypted)
            case _:
                return await self._inner.read(path)

    async def exists(self, path: str) -> bool:
        if self._protection_level == ProtectionLevel.ENCRYPT:
            return await self._inner.exists(f"{path}.enc")
        return await self._inner.exists(path)

    async def delete(self, path: str) -> None:
        if self._protection_level == ProtectionLevel.ENCRYPT:
            await self._inner.delete(f"{path}.enc")
        else:
            await self._inner.delete(path)
            if self._protection_level == ProtectionLevel.HASH:
                try:
                    await self._inner.delete(f"{path}.hash")
                except FileNotFoundError:
                    pass
            elif self._protection_level == ProtectionLevel.HMAC:
                try:
                    await self._inner.delete(f"{path}.hmac")
                except FileNotFoundError:
                    pass

    async def list(self, prefix: str) -> list[str]:
        entries = await self._inner.list(prefix)
        result = []
        for e in entries:
            if e.endswith(".enc"):
                if self._protection_level == ProtectionLevel.ENCRYPT:
                    result.append(e.removesuffix(".enc"))
            elif not any(e.endswith(ext) for ext in (".hash", ".hmac")):
                result.append(e)
        return result

    def get_uri(self, path: str) -> str:
        if self._protection_level == ProtectionLevel.ENCRYPT:
            return self._inner.get_uri(f"{path}.enc")
        return self._inner.get_uri(path)


def _derive_key(master: bytes, info: bytes, length: int) -> bytes:
    """Derive a purpose-specific key from a master secret using HKDF-SHA256."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=info,
    ).derive(master)


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


def create_s3_operator(s3: S3Settings, root: str = "/") -> opendal.AsyncOperator:
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
    MIN_FREE_RESERVE_BYTES = 1 * 1024 * 1024  # 1 MiB headroom for FS metadata, sidecars, Fernet expansion
    COMPRESSION_SUFFIX = ".zst"
    # Below this size, compression overhead (zstd header + framing) typically inflates the file,
    # so we write plain. This also keeps integrity sidecars (.hash, .hmac, ~128 bytes) uncompressed.
    COMPRESS_MIN_BYTES = 1024

    def __init__(
        self,
        store_path: str,
        compress: bool = False,
        compress_level: int = 3,
    ):
        path = pathlib.Path(store_path)
        if not path.is_absolute():
            path = pathlib.Path.cwd() / store_path
        self.store_path = str(path)
        self.compress = compress
        self.compress_level = compress_level
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)

    def _ensure_free_space(self, num_bytes: int) -> None:
        usage = shutil.disk_usage(self.store_path)
        required = num_bytes + self.MIN_FREE_RESERVE_BYTES
        if usage.free < required:
            raise OSError(
                errno.ENOSPC,
                f"insufficient disk space at {self.store_path}: need {required} bytes, {usage.free} free",
            )

    def _get_normalized_path(self, path: str) -> pathlib.Path:
        """Get the full filesystem path for a given key, creating shard directory if needed.

        Shard directory is computed from the stem (before first dot) so that
        sidecar files (.hash, .hmac) and encrypted files (.enc) are
        co-located with their data file.
        """
        stem = path.split(".")[0] if "." in path else path
        subdir = stem[-self.SHARD_SUFFIX_LENGTH :]
        shard_dir = pathlib.Path(self.store_path) / subdir
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / path

    async def read(self, path: str) -> bytes:
        zst_path = self._get_normalized_path(path + self.COMPRESSION_SUFFIX)
        if await aos.path.exists(zst_path):
            return await asyncio.to_thread(self._read_compressed_sync, zst_path)
        norm_path = self._get_normalized_path(path)
        async with aiofiles.open(norm_path, "rb") as f:
            return await f.read()

    async def exists(self, path: str) -> bool:
        zst_path = self._get_normalized_path(path + self.COMPRESSION_SUFFIX)
        if await aos.path.exists(zst_path):
            return True
        norm_path = self._get_normalized_path(path)
        return await aos.path.exists(norm_path)

    async def write(self, path: str, data: bytes) -> None:
        # Conservative: plaintext size is an upper bound on compressed-on-disk size,
        # so checking against plaintext keeps the guarantee even when compressing.
        self._ensure_free_space(len(data))
        if self.compress and len(data) >= self.COMPRESS_MIN_BYTES:
            target = self._get_normalized_path(path + self.COMPRESSION_SUFFIX)
            write_succeeded = False
            try:
                await asyncio.to_thread(self._write_compressed_sync, target, data)
                write_succeeded = True
            finally:
                if not write_succeeded:
                    try:
                        await aos.unlink(target)
                    except FileNotFoundError:
                        pass
        else:
            norm_path = self._get_normalized_path(path)
            write_succeeded = False
            try:
                async with aiofiles.open(norm_path, "wb") as f:
                    await f.write(data)
                write_succeeded = True
            finally:
                if not write_succeeded:
                    try:
                        await aos.unlink(norm_path)
                    except FileNotFoundError:
                        pass

    def _write_compressed_sync(self, target: pathlib.Path, data: bytes) -> None:
        cctx = zstandard.ZstdCompressor(level=self.compress_level)
        with open(target, "wb") as f:
            with cctx.stream_writer(f) as writer:
                writer.write(data)

    def _read_compressed_sync(self, target: pathlib.Path) -> bytes:
        dctx = zstandard.ZstdDecompressor()
        with open(target, "rb") as f:
            with dctx.stream_reader(f) as reader:
                return reader.read()

    async def delete(self, path: str) -> None:
        zst_path = self._get_normalized_path(path + self.COMPRESSION_SUFFIX)
        norm_path = self._get_normalized_path(path)
        zst_exists = await aos.path.exists(zst_path)
        plain_exists = await aos.path.exists(norm_path)
        if not zst_exists and not plain_exists:
            raise FileNotFoundError(path)
        if zst_exists:
            await aos.unlink(zst_path)
        if plain_exists:
            await aos.unlink(norm_path)

    async def list(self, prefix: str) -> list[str]:
        base_path = pathlib.Path(self.store_path)
        files = await recursive_listdir(base_path)
        seen: set[str] = set()
        result: list[str] = []
        for f in files:
            name = f.name
            logical = name.removesuffix(self.COMPRESSION_SUFFIX) if name.endswith(self.COMPRESSION_SUFFIX) else name
            if logical not in seen:
                seen.add(logical)
                result.append(logical)
        return result

    def get_uri(self, path: str) -> str:
        zst_path = self._get_normalized_path(path + self.COMPRESSION_SUFFIX)
        if zst_path.exists():
            return zst_path.as_uri()
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
        s3_root = f"/{getattr(settings, f'{st}_store_dir')}/{root}"
        raw_op = create_s3_operator(settings.artifact_s3, s3_root)
        return OpenDALAdapter(raw_op, root)
    elif target == "fs":
        fs_root = f"{settings.file_store_dir}/{getattr(settings, f'{st}_store_dir')}/{root}"
        compress = artifact_type.value in settings.file_compression_artifacts
        op: StorageOperator = FileStorageOperator(
            fs_root,
            compress=compress,
            compress_level=settings.file_compression_level,
        )
        if settings.file_protection_level != ProtectionLevel.NONE:
            secret = settings.file_secret.get_secret_value() if settings.file_secret else None
            op = ProtectedStorageOperator(op, settings.file_protection_level, secret)
        return op
    elif target == "db":
        return DBStorageOperator(st, root)
    else:
        raise ValueError(f"Unknown target {target}")


def _get_store_dirs() -> list[str]:
    """Return the list of artifact store subdirectory setting names."""
    return [
        "document_store_dir",
        "parsed_markdown_store_dir",
        "parsed_json_store_dir",
        "chunks_store_dir",
        "embeddings_store_dir",
    ]


async def apply_file_protection(
    target_level: ProtectionLevel,
    secret: str | None = None,
) -> dict[str, int]:
    """Apply a protection level to all files in the filesystem store.

    Walks every artifact store directory, reads each file (decrypting if
    currently encrypted), then writes it with the target protection and
    cleans up old protection artifacts.

    Uses ``FILE_SECRET`` from settings to decrypt existing encrypted files
    and to apply HMAC/ENCRYPT protection.

    Parameters
    ----------
    target_level:
        The protection level to apply.
    secret:
        Master secret.  Required when *target_level* is HMAC or ENCRYPT,
        or when existing encrypted files must be decrypted.

    Returns
    -------
    dict with keys ``processed``, ``skipped``, ``errors``.
    """
    settings = get_settings()
    if settings.file_store_target != "fs":
        raise ValueError("Protection migration is only supported for filesystem storage (FILE_STORE_TARGET=fs)")

    # Build crypto primitives from secret (if provided)
    fernet_old: Fernet | None = None
    fernet_new: Fernet | None = None
    hmac_key_new: bytes = b""

    if secret:
        master = secret.encode("utf-8")
        hmac_key_new = _derive_key(master, b"hmac-sha512", 64)
        fernet_raw = _derive_key(master, b"fernet-v1", 32)
        fernet_new = Fernet(base64.urlsafe_b64encode(fernet_raw))
        fernet_old = fernet_new  # same secret decrypts existing files

    if (
        target_level
        in (
            ProtectionLevel.HMAC,
            ProtectionLevel.ENCRYPT,
        )
        and not secret
    ):
        raise ValueError(f"secret required for {target_level}")

    base = pathlib.Path(settings.file_store_dir)
    stats: dict[str, int] = {"processed": 0, "skipped": 0, "errors": 0}

    for attr in _get_store_dirs():
        store_subdir = getattr(settings, attr)
        dir_path = base / store_subdir
        if not dir_path.exists():
            continue

        files = await recursive_listdir(dir_path)

        # Collect logical names, skipping sidecars (handled with parent)
        seen: set[str] = set()
        for file_path in files:
            name = file_path.name
            if name.endswith((".hash", ".hmac")):
                continue
            if name.endswith(".enc"):
                seen.add(name.removesuffix(".enc"))
            else:
                seen.add(name)

        for logical_name in sorted(seen):
            shard = logical_name.split(".")[0] if "." in logical_name else logical_name
            shard_suffix = shard[-FileStorageOperator.SHARD_SUFFIX_LENGTH :]
            shard_dir = dir_path / shard_suffix

            enc_path = shard_dir / f"{logical_name}.enc"
            plain_path = shard_dir / logical_name
            hash_path = shard_dir / f"{logical_name}.hash"
            hmac_path = shard_dir / f"{logical_name}.hmac"

            try:
                # --- Read the data (decrypt if needed) ---
                if enc_path.exists():
                    if not fernet_old:
                        logger.error(
                            "cannot decrypt %s without FILE_SECRET",
                            enc_path,
                        )
                        stats["errors"] += 1
                        continue
                    async with aiofiles.open(enc_path, "rb") as f:
                        data = fernet_old.decrypt(await f.read())
                elif plain_path.exists():
                    async with aiofiles.open(plain_path, "rb") as f:
                        data = await f.read()
                else:
                    stats["skipped"] += 1
                    continue

                # --- Write with target protection ---
                match target_level:
                    case ProtectionLevel.NONE:
                        if not plain_path.exists():
                            async with aiofiles.open(plain_path, "wb") as f:
                                await f.write(data)
                    case ProtectionLevel.HASH:
                        if not plain_path.exists():
                            async with aiofiles.open(plain_path, "wb") as f:
                                await f.write(data)
                        digest = hashlib.sha512(data).hexdigest()
                        async with aiofiles.open(hash_path, "wb") as f:
                            await f.write(digest.encode())
                    case ProtectionLevel.HMAC:
                        if not plain_path.exists():
                            async with aiofiles.open(plain_path, "wb") as f:
                                await f.write(data)
                        digest = hmac_mod.new(hmac_key_new, data, hashlib.sha512).hexdigest()
                        async with aiofiles.open(hmac_path, "wb") as f:
                            await f.write(digest.encode())
                    case ProtectionLevel.ENCRYPT:
                        encrypted = fernet_new.encrypt(data)
                        async with aiofiles.open(enc_path, "wb") as f:
                            await f.write(encrypted)

                # --- Clean up old artifacts ---
                if target_level != ProtectionLevel.ENCRYPT and enc_path.exists():
                    enc_path.unlink()
                if target_level != ProtectionLevel.HASH and hash_path.exists():
                    hash_path.unlink()
                if target_level != ProtectionLevel.HMAC and hmac_path.exists():
                    hmac_path.unlink()
                if target_level == ProtectionLevel.ENCRYPT and plain_path.exists():
                    plain_path.unlink()

                stats["processed"] += 1

            except Exception:
                logger.exception("failed to migrate %s", logical_name)
                stats["errors"] += 1

    return stats
