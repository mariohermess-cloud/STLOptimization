"""Mesh validation, repair and metric extraction.

Units: STL carries no unit information. The whole application assumes
**millimetres**, which is the de-facto convention for FDM slicers. Every
derived quantity follows from that (mm^2, mm^3, g, N, MPa).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import trimesh

from app.core.errors import InvalidMeshError

logger = logging.getLogger(__name__)


@dataclass
class MeshValidation:
    is_watertight: bool
    is_winding_consistent: bool
    is_volume: bool
    euler_number: int
    body_count: int
    boundary_edge_count: int
    degenerate_face_count: int
    duplicate_face_count: int
    repairs_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def volume_is_reliable(self) -> bool:
        return self.is_watertight and self.is_winding_consistent


@dataclass
class GeometryMetrics:
    triangle_count: int
    vertex_count: int
    bounding_box_min: list[float]
    bounding_box_max: list[float]
    dimensions: list[float]
    volume_mm3: float
    surface_area_mm2: float
    convex_hull_volume_mm3: float
    solidity: float
    center_of_mass: list[float]
    centroid: list[float]
    aspect_ratio: float
    principal_inertia_components: list[float]
    principal_inertia_axes: list[list[float]]
    pca_axes: list[list[float]]
    pca_extents: list[float]
    min_wall_sample_mm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "triangle_count": self.triangle_count,
            "vertex_count": self.vertex_count,
            "bounding_box_min": self.bounding_box_min,
            "bounding_box_max": self.bounding_box_max,
            "dimensions": self.dimensions,
            "volume_mm3": self.volume_mm3,
            "surface_area_mm2": self.surface_area_mm2,
            "convex_hull_volume_mm3": self.convex_hull_volume_mm3,
            "solidity": self.solidity,
            "center_of_mass": self.center_of_mass,
            "centroid": self.centroid,
            "aspect_ratio": self.aspect_ratio,
            "principal_inertia_components": self.principal_inertia_components,
            "principal_inertia_axes": self.principal_inertia_axes,
            "pca_axes": self.pca_axes,
            "pca_extents": self.pca_extents,
        }


def validate_and_repair(mesh: trimesh.Trimesh, *, repair: bool = True) -> MeshValidation:
    """Inspect the mesh and apply conservative, non-destructive repairs.

    Repairs performed, in order:

    1. ``merge_vertices`` - welds numerically identical vertices (STL stores
       every triangle independently, so this is always needed).
    2. removal of degenerate (zero area) and duplicated faces.
    3. ``fix_winding`` - makes the winding consistent. Only attempted when it
       is not already consistent.
    4. ``fill_holes`` - closes small boundary loops. Only attempted when the
       mesh is not watertight; the result is re-checked afterwards.
    5. ``fix_inversion`` - flips the normals of a mesh whose enclosed volume
       comes out negative, i.e. one that is inside out.

    Nothing here changes the shape of a valid mesh.
    """
    repairs: list[str] = []
    warnings: list[str] = []

    before_faces = len(mesh.faces)
    mesh.merge_vertices()

    degenerate_mask = ~mesh.nondegenerate_faces(height=1e-9)
    degenerate = int(np.count_nonzero(degenerate_mask))
    duplicate = 0
    if repair and degenerate:
        mesh.update_faces(~degenerate_mask)
        repairs.append(f"removed {degenerate} degenerate (zero-area) triangles")

    if repair:
        unique_mask = mesh.unique_faces()
        duplicate = int(np.count_nonzero(~unique_mask))
        if duplicate:
            mesh.update_faces(unique_mask)
            repairs.append(f"removed {duplicate} duplicated triangles")

    if repair and not mesh.is_winding_consistent:
        trimesh.repair.fix_winding(mesh)
        if mesh.is_winding_consistent:
            repairs.append("repaired inconsistent triangle winding")

    if repair and not mesh.is_watertight:
        try:
            trimesh.repair.fill_holes(mesh)
        except Exception as exc:  # noqa: BLE001
            logger.info("fill_holes failed", extra={"reason": str(exc)})
        if mesh.is_watertight:
            repairs.append("closed open boundary loops (hole filling)")
        else:
            warnings.append(
                "The mesh is not watertight. Volume, mass and internal analyses "
                "are approximate for this model."
            )

    if len(mesh.faces) == 0:
        raise InvalidMeshError("All triangles were degenerate; there is no usable geometry.")
    if len(mesh.faces) != before_faces:
        logger.info(
            "mesh repaired",
            extra={"operation": "repair", "faces_before": before_faces, "faces_after": len(mesh.faces)},
        )

    try:
        # Edges referenced by exactly one triangle are open boundary edges.
        boundary_edges = int(
            len(trimesh.grouping.group_rows(mesh.edges_sorted, require_count=1))
        )
    except Exception:  # noqa: BLE001
        boundary_edges = 0

    try:
        body_count = int(mesh.body_count)
    except Exception:  # noqa: BLE001
        body_count = 1

    if repair and float(mesh.volume) < 0.0:
        # Every triangle wound the wrong way round: the enclosed volume comes
        # out negative. Splitting into bodies is only paid for when the model
        # actually has more than one.
        trimesh.repair.fix_inversion(mesh, multibody=body_count > 1)
        repairs.append("flipped inverted face normals")

    if body_count > 1:
        warnings.append(
            f"The model contains {body_count} separate bodies. They are analysed "
            "together as one part."
        )

    if not mesh.is_winding_consistent:
        warnings.append("Triangle winding is inconsistent; face normals may be unreliable.")

    return MeshValidation(
        is_watertight=bool(mesh.is_watertight),
        is_winding_consistent=bool(mesh.is_winding_consistent),
        is_volume=bool(mesh.is_volume),
        euler_number=int(mesh.euler_number),
        body_count=body_count,
        boundary_edge_count=boundary_edges,
        degenerate_face_count=degenerate,
        duplicate_face_count=duplicate,
        repairs_applied=repairs,
        warnings=warnings,
    )


def compute_metrics(mesh: trimesh.Trimesh, validation: MeshValidation) -> GeometryMetrics:
    """Extract the geometric quantities shown in the model information panel."""
    bounds = np.asarray(mesh.bounds, dtype=float)
    extents = bounds[1] - bounds[0]

    hull_volume = float(abs(mesh.convex_hull.volume))
    if validation.volume_is_reliable:
        volume = float(abs(mesh.volume))
    else:
        # A non-watertight mesh has no well defined enclosed volume. The
        # divergence-theorem value is still the best available estimate, but it
        # is clamped to the convex hull to avoid absurd numbers.
        volume = float(min(abs(mesh.volume), hull_volume)) if hull_volume > 0 else 0.0

    try:
        com = np.asarray(mesh.center_mass, dtype=float)
        if not np.isfinite(com).all():
            raise ValueError
    except Exception:  # noqa: BLE001
        com = np.asarray(mesh.centroid, dtype=float)

    try:
        principal_components = np.asarray(mesh.principal_inertia_components, dtype=float)
        principal_axes = np.asarray(mesh.principal_inertia_vectors, dtype=float)
        if not np.isfinite(principal_components).all():
            raise ValueError
    except Exception:  # noqa: BLE001
        principal_components = np.zeros(3)
        principal_axes = np.eye(3)

    pca_axes, pca_extents = _pca_axes(mesh)

    nonzero = extents[extents > 1e-9]
    aspect = float(np.max(extents) / np.min(nonzero)) if nonzero.size == 3 else float("inf")

    return GeometryMetrics(
        triangle_count=int(len(mesh.faces)),
        vertex_count=int(len(mesh.vertices)),
        bounding_box_min=[float(v) for v in bounds[0]],
        bounding_box_max=[float(v) for v in bounds[1]],
        dimensions=[float(v) for v in extents],
        volume_mm3=volume,
        surface_area_mm2=float(mesh.area),
        convex_hull_volume_mm3=hull_volume,
        solidity=float(volume / hull_volume) if hull_volume > 1e-9 else 0.0,
        center_of_mass=[float(v) for v in com],
        centroid=[float(v) for v in np.asarray(mesh.centroid, dtype=float)],
        aspect_ratio=aspect,
        principal_inertia_components=[float(v) for v in principal_components],
        principal_inertia_axes=[[float(v) for v in row] for row in principal_axes],
        pca_axes=[[float(v) for v in row] for row in pca_axes],
        pca_extents=[float(v) for v in pca_extents],
    )


def _pca_axes(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted principal component axes of the surface.

    Face centroids weighted by face area approximate the surface distribution
    far better than raw vertices, which are biased towards densely tessellated
    regions.
    """
    centers = np.asarray(mesh.triangles_center, dtype=float)
    weights = np.asarray(mesh.area_faces, dtype=float)
    total = float(weights.sum())
    if total <= 0 or len(centers) < 3:
        return np.eye(3), np.zeros(3)

    mean = (centers * weights[:, None]).sum(axis=0) / total
    centered = centers - mean
    cov = (centered * weights[:, None]).T @ centered / total
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    order = np.argsort(eigenvalues)[::-1]
    axes = eigenvectors[:, order].T
    # Report the extent of the part along each principal axis (mm), which is
    # more useful to a user than the eigenvalue itself.
    projected = np.asarray(mesh.vertices, dtype=float) @ axes.T
    extents = projected.max(axis=0) - projected.min(axis=0)
    # Fix handedness so the triad is right-handed.
    if np.linalg.det(axes) < 0:
        axes[2] = -axes[2]
    return axes, extents


def decimate_for_analysis(mesh: trimesh.Trimesh, face_budget: int) -> tuple[trimesh.Trimesh, bool]:
    """Return a mesh small enough for the orientation search.

    APPROXIMATION: orientation scoring is evaluated on a simplified copy when
    the model exceeds the face budget. Areas and normals of the simplified mesh
    differ slightly from the original, so scores carry a small extra error.
    The full resolution mesh is still used for reported metrics and for section
    analysis. The confidence score is reduced when this path is taken.
    """
    if len(mesh.faces) <= face_budget:
        return mesh, False
    try:
        simplified = mesh.simplify_quadric_decimation(face_count=face_budget)
        if simplified is not None and len(simplified.faces) >= 4:
            simplified.merge_vertices()
            return simplified, True
    except Exception as exc:  # noqa: BLE001
        logger.info("decimation failed, using full mesh", extra={"reason": str(exc)})
    return mesh, False
