"""Precomputed mesh arrays shared by every orientation evaluation.

The orientation search evaluates hundreds of build directions against the same
mesh. Anything that does not depend on the build direction is computed exactly
once and stored here: face normals, face areas, face centroids, the convex
hull (used for heights and footprints) and the mass properties.

Nothing in the pipeline ever rotates the mesh itself. A build direction ``d``
in the part frame is enough for every printability metric, which is what makes
the search affordable on large models.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass
class PreparedMesh:
    mesh: trimesh.Trimesh
    vertices: np.ndarray
    face_normals: np.ndarray
    face_areas: np.ndarray
    face_centers: np.ndarray
    hull_points: np.ndarray
    center_of_mass: np.ndarray
    volume_mm3: float
    area_mm2: float
    extents: np.ndarray

    @property
    def face_count(self) -> int:
        return int(len(self.face_areas))


def prepare(mesh: trimesh.Trimesh, volume_mm3: float | None = None) -> PreparedMesh:
    try:
        hull_points = np.asarray(mesh.convex_hull.vertices, dtype=float)
    except Exception:  # noqa: BLE001
        hull_points = np.asarray(mesh.vertices, dtype=float)
    if len(hull_points) < 4:
        hull_points = np.asarray(mesh.vertices, dtype=float)

    try:
        com = np.asarray(mesh.center_mass, dtype=float)
        if not np.isfinite(com).all():
            raise ValueError
    except Exception:  # noqa: BLE001
        com = np.asarray(mesh.centroid, dtype=float)

    volume = float(volume_mm3) if volume_mm3 is not None else float(abs(mesh.volume))

    return PreparedMesh(
        mesh=mesh,
        vertices=np.asarray(mesh.vertices, dtype=float),
        face_normals=np.asarray(mesh.face_normals, dtype=float),
        face_areas=np.asarray(mesh.area_faces, dtype=float),
        face_centers=np.asarray(mesh.triangles_center, dtype=float),
        hull_points=hull_points,
        center_of_mass=com,
        volume_mm3=volume,
        area_mm2=float(mesh.area),
        extents=np.asarray(mesh.extents, dtype=float),
    )


def orthonormal_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors spanning the plane perpendicular to ``direction``."""
    d = np.asarray(direction, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    reference = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, reference)
    u = u / max(np.linalg.norm(u), 1e-12)
    v = np.cross(d, u)
    return u, v / max(np.linalg.norm(v), 1e-12)
