"""Artifact storage: a local directory (default, local development and tests) or Amazon S3 (AWS deployment)."""
from app.storage.base import ArtifactStore, StorageError, StoredObject, build_store
from app.storage.local import LocalArtifactStore
from app.storage.s3 import S3ArtifactStore

__all__ = ["ArtifactStore", "StorageError", "StoredObject", "build_store", "LocalArtifactStore", "S3ArtifactStore"]
