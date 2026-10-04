"""Storage interface. Keys are relative paths such as `evidence-packs/2026/09/29/<decision>/<artifact>.json`.

Implementations never return credentials or presigned URLs; downloads go through the API so the bucket stays private.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,400}$")


class StorageError(RuntimeError):
    """A storage operation failed. The message is safe to log and store (no secrets, no bucket credentials)."""


@dataclass(frozen=True)
class StoredObject:
    backend: str
    key: str
    size_bytes: int
    sha256: str


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_key(key: str) -> str:
    if not _SAFE_KEY.match(key) or ".." in key.split("/") or "//" in key:
        raise StorageError("invalid artifact key")
    return key


class ArtifactStore(ABC):
    backend: str = "base"

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str = "application/json", metadata: dict[str, str] | None = None) -> StoredObject: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def describe(self) -> dict[str, str]:
        """Non-secret configuration for readiness output."""


def build_store(backend: str | None = None) -> ArtifactStore:
    from app.config import settings
    from app.storage.local import LocalArtifactStore
    from app.storage.s3 import S3ArtifactStore

    backend = (backend or settings.storage_backend).lower()
    if backend == "local":
        return LocalArtifactStore(settings.storage_local_dir)
    if backend == "s3":
        if not settings.artifacts_bucket:
            raise StorageError("STORAGE_BACKEND=s3 but ARTIFACTS_BUCKET is not set")
        return S3ArtifactStore(bucket=settings.artifacts_bucket, region=settings.aws_region)
    raise StorageError(f"unknown storage backend '{backend}'")
