"""Local-directory artifact store (default outside AWS)."""
from __future__ import annotations

import os
from pathlib import Path

from app.storage.base import ArtifactStore, StorageError, StoredObject, check_key, sha256_hex


class LocalArtifactStore(ArtifactStore):
    backend = "local"

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / check_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError("artifact key escapes the storage directory")
        return path

    def put(self, key, data, content_type="application/json", metadata=None) -> StoredObject:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic on POSIX
        except OSError as exc:
            raise StorageError(f"local write failed ({type(exc).__name__})") from None
        return StoredObject(self.backend, key, len(data), sha256_hex(data))

    def get(self, key) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise StorageError("artifact not found in storage") from None
        except OSError as exc:
            raise StorageError(f"local read failed ({type(exc).__name__})") from None

    def describe(self) -> dict[str, str]:
        return {"backend": self.backend, "location": "local directory"}
