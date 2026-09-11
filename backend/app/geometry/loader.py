"""Safe STL ingestion.

Security posture (see ``docs/architecture.md``):

* The uploaded bytes are only ever parsed as STL geometry. Nothing from the
  file is executed, evaluated or used as a path.
* The client supplied filename is never used on disk. Storage keys are server
  generated UUIDs; the original name is kept only as a sanitised display label.
* The format is decided by inspecting the bytes, not by the file extension.
* Size and triangle-count limits are enforced before and during parsing.
"""

from __future__ import annotations

import logging
import re
import struct
from dataclasses import dataclass
from io import BytesIO

import numpy as np
import trimesh

from app.core.config import settings
from app.core.errors import FileTooLargeError, InvalidMeshError, UnsupportedFileError

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ]+")

BINARY_STL_HEADER = 84  # 80 byte header + uint32 triangle count


@dataclass(frozen=True)
class StlFormat:
    kind: str  # "binary" | "ascii"
    declared_triangles: int | None


def sanitize_filename(name: str | None) -> str:
    """Return a short, printable label. Never used to touch the filesystem."""
    if not name:
        return "model.stl"
    base = name.replace("\\", "/").split("/")[-1]
    base = _SAFE_NAME.sub("_", base).strip() or "model.stl"
    return base[:120]


def detect_stl_format(data: bytes) -> StlFormat:
    """Decide binary vs ASCII from the bytes themselves.

    The ``solid`` keyword is not sufficient: many exporters write it into the
    80 byte header of a binary file. The reliable test is whether the declared
    triangle count matches the file length for the binary layout.
    """
    if len(data) < 15:
        raise InvalidMeshError("The file is too small to contain any geometry.")

    if len(data) >= BINARY_STL_HEADER:
        (count,) = struct.unpack("<I", data[80:84])
        expected = BINARY_STL_HEADER + count * 50
        if expected == len(data):
            return StlFormat("binary", count)
        # Some writers append padding; accept a small tail.
        if 0 < count and expected <= len(data) <= expected + 128:
            return StlFormat("binary", count)

    head = data[:2048].lstrip()
    if head[:5].lower() == b"solid" and b"facet" in data[:8192].lower():
        return StlFormat("ascii", None)

    if len(data) >= BINARY_STL_HEADER:
        (count,) = struct.unpack("<I", data[80:84])
        raise InvalidMeshError(
            "The file is not a valid STL: the binary triangle count "
            f"({count}) does not match the file length ({len(data)} bytes), and "
            "the file does not parse as ASCII STL."
        )
    raise UnsupportedFileError()


def load_stl(data: bytes) -> tuple[trimesh.Trimesh, StlFormat]:
    """Parse STL bytes into a single :class:`trimesh.Trimesh`."""
    if len(data) > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"The file is {len(data) / 1e6:.1f} MB; the limit is "
            f"{settings.max_upload_bytes / 1e6:.0f} MB."
        )

    fmt = detect_stl_format(data)
    if fmt.declared_triangles and fmt.declared_triangles > settings.max_faces:
        raise FileTooLargeError(
            f"The mesh declares {fmt.declared_triangles:,} triangles; the limit "
            f"is {settings.max_faces:,}."
        )

    try:
        mesh = trimesh.load(
            BytesIO(data),
            file_type="stl",
            process=True,
            validate=False,
            force="mesh",
        )
    except Exception as exc:  # noqa: BLE001 - trimesh raises many types
        logger.warning("stl parse failed", extra={"operation": "load_stl", "reason": str(exc)})
        raise InvalidMeshError(
            "The STL file could not be parsed. It may be truncated or corrupted."
        ) from exc

    if isinstance(mesh, trimesh.Scene):
        geometries = [g for g in mesh.geometry.values() if isinstance(g, trimesh.Trimesh)]
        if not geometries:
            raise InvalidMeshError("The file does not contain any triangle geometry.")
        mesh = trimesh.util.concatenate(geometries)

    if not isinstance(mesh, trimesh.Trimesh):
        raise InvalidMeshError("The file does not contain a triangle mesh.")

    if len(mesh.faces) == 0:
        raise InvalidMeshError("The mesh contains no triangles.")
    if len(mesh.faces) > settings.max_faces:
        raise FileTooLargeError(
            f"The mesh has {len(mesh.faces):,} triangles; the limit is {settings.max_faces:,}."
        )
    if not np.isfinite(mesh.vertices).all():
        raise InvalidMeshError("The mesh contains non-finite (NaN or infinite) coordinates.")

    extents = mesh.extents
    if extents is None or float(np.max(extents)) <= 0.0:
        raise InvalidMeshError("The mesh has zero size in every direction.")

    return mesh, fmt
