"""Amazon S3 artifact store.

* Credentials come from the standard boto3 chain (the ECS task role on AWS). None are configured in REACT.
* Objects are written with SSE-S3 encryption; the bucket itself blocks public access and enforces TLS (see infra/).
* The IAM task role is scoped to s3:PutObject / s3:GetObject on the evidence-pack prefix only.
* No retries beyond botocore's standard mode (3 attempts): a failure is recorded, never looped.
"""
from __future__ import annotations

from typing import Any

from app.storage.base import ArtifactStore, StorageError, StoredObject, check_key, sha256_hex


class S3ArtifactStore(ArtifactStore):
    backend = "s3"

    def __init__(self, bucket: str, region: str, client: Any = None) -> None:
        self.bucket, self.region = bucket, region
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client("s3", region_name=self.region, config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"}))
        return self._client

    def put(self, key, data, content_type="application/json", metadata=None) -> StoredObject:
        check_key(key)
        digest = sha256_hex(data)
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256", Metadata={**(metadata or {}), "sha256": digest})
        except Exception as exc:  # noqa: BLE001  (botocore raises many classes; never echo the raw message)
            raise StorageError(f"S3 upload failed ({_code(exc)})") from None
        return StoredObject(self.backend, key, len(data), digest)

    def get(self, key) -> bytes:
        check_key(key)
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except Exception as exc:  # noqa: BLE001
            code = _code(exc)
            raise StorageError("artifact not found in storage" if code in ("NoSuchKey", "404") else f"S3 download failed ({code})") from None

    def describe(self) -> dict[str, str]:
        return {"backend": self.backend, "location": "s3 bucket (configured)"}


def _code(exc: Exception) -> str:
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        return str(resp.get("Error", {}).get("Code") or type(exc).__name__)
    return type(exc).__name__
