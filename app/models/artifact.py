"""Phase 6: stored artifacts (evidence packs).

An evidence pack is a JSON snapshot written when a human approves or rejects a decision. It freezes what the
reviewer saw: the decision, the investigation conclusion, the cited evidence, the impact-engine figures, the
visible agent trace and the data provenance. The bytes live in the artifact store (a local directory, or S3 on
AWS). This row records where they are and their SHA-256 so a download can be checked for tampering.
A failed upload is recorded with status FAILED; it never blocks the approval itself.
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models._types import JSONType


class Artifact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "artifacts"

    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)  # evidence_pack
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("decisions.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)  # decision.approved | decision.rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # STORED | FAILED
    storage_backend: Mapped[str] = mapped_column(String(20), nullable=False)  # local | s3
    location: Mapped[str | None] = mapped_column(String(500))  # object key (never a presigned URL or credential)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False, default="application/json")
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    artifact_metadata: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
