"""In-process model store.

Design notes
------------
* Uploaded bytes are written to ``settings.storage_dir`` under a
  **server generated UUID**. The client's filename never touches the
  filesystem, which removes path traversal as a class of bug.
* Parsed meshes live in memory. The store is bounded by ``max_models`` and by
  ``model_ttl_seconds``; whichever triggers first evicts the record and deletes
  its file.
* Identical uploads are deduplicated by SHA-256 of the file content, so
  re-uploading the same part reuses the existing analysis.

This is deliberately a single-process store. The MVP runs one backend
container; moving to several workers means replacing this module with a shared
store (Redis plus object storage), which is why everything goes through it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import trimesh

from app.core.config import settings
from app.core.errors import ModelNotFoundError
from app.geometry.analyzer import GeometryMetrics, MeshValidation
from app.geometry.prepared import PreparedMesh

logger = logging.getLogger(__name__)


@dataclass
class ModelRecord:
    id: str
    filename: str
    size_bytes: int
    stl_format: str
    sha256: str
    path: Path
    uploaded_at: float
    mesh: trimesh.Trimesh
    analysis_mesh: trimesh.Trimesh
    prepared: PreparedMesh
    validation: MeshValidation
    metrics: GeometryMetrics
    decimated: bool
    duplicate_of: str | None = None
    last_access: float = field(default_factory=time.time)
    results: dict[str, Any] = field(default_factory=dict)
    _stl_cache: bytes | None = None

    def touch(self) -> None:
        self.last_access = time.time()

    def processed_stl(self) -> bytes:
        """The repaired mesh as binary STL.

        The viewer loads exactly this mesh so that face indices sent back to
        the API (face selection, fixed surfaces, loaded surfaces) address the
        same triangles the backend analysed.
        """
        if self._stl_cache is None:
            self._stl_cache = self.mesh.export(file_type="stl")
        return self._stl_cache


class ModelStore:
    def __init__(self) -> None:
        self._records: dict[str, ModelRecord] = {}
        self._by_hash: dict[str, str] = {}
        self._lock = threading.RLock()
        self._dir = Path(settings.storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- writes ------------------------------------------------------------
    def add(self, data: bytes, **fields: Any) -> ModelRecord:
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            existing_id = self._by_hash.get(digest)
            if existing_id and existing_id in self._records:
                record = self._records[existing_id]
                record.touch()
                logger.info(
                    "duplicate upload reused",
                    extra={"operation": "upload", "model_id": record.id},
                )
                return record

            model_id = uuid.uuid4().hex
            path = self._dir / f"{model_id}.stl"
            path.write_bytes(data)

            record = ModelRecord(
                id=model_id,
                sha256=digest,
                path=path,
                uploaded_at=time.time(),
                size_bytes=len(data),
                **fields,
            )
            self._records[model_id] = record
            self._by_hash[digest] = model_id
            self._evict_if_needed()
            return record

    # -- reads -------------------------------------------------------------
    def get(self, model_id: str) -> ModelRecord:
        with self._lock:
            record = self._records.get(model_id)
            if record is None:
                raise ModelNotFoundError()
            record.touch()
            return record

    def list(self) -> list[ModelRecord]:
        with self._lock:
            return list(self._records.values())

    # -- maintenance -------------------------------------------------------
    def cleanup(self) -> int:
        """Remove expired records. Returns how many were removed."""
        cutoff = time.time() - settings.model_ttl_seconds
        removed = 0
        with self._lock:
            for model_id in [k for k, v in self._records.items() if v.last_access < cutoff]:
                self._remove(model_id)
                removed += 1
        if removed:
            logger.info("expired models removed", extra={"operation": "cleanup", "count": removed})
        return removed

    def _evict_if_needed(self) -> None:
        while len(self._records) > settings.max_models:
            oldest = min(self._records.values(), key=lambda record: record.last_access)
            self._remove(oldest.id)

    def _remove(self, model_id: str) -> None:
        record = self._records.pop(model_id, None)
        if record is None:
            return
        self._by_hash.pop(record.sha256, None)
        try:
            record.path.unlink(missing_ok=True)
        except OSError as exc:  # noqa: PERF203
            logger.warning(
                "could not delete model file",
                extra={"operation": "cleanup", "model_id": model_id, "reason": str(exc)},
            )

    def clear(self) -> None:
        with self._lock:
            for model_id in list(self._records):
                self._remove(model_id)


store = ModelStore()
