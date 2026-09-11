"""Detection of potentially critical geometric regions.

These are **geometric** risk indicators, not stress results. Without a finite
element analysis the application does not claim to know where stress
concentrates; it reports where the geometry has the properties that usually
cause trouble - thin walls for the nozzle in use, abrupt changes of
cross-section, large unsupported overhangs, small internal radii, and the
section that beam theory says carries the highest load.

Every region is labelled "potentially critical geometric region" in the UI for
that reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from app.geometry.prepared import PreparedMesh
from app.geometry.sections import section_at
from app.schemas import CriticalRegion

logger = logging.getLogger(__name__)

#: A wall needs at least two extrusion lines to be printed as a wall at all.
MIN_WALL_LINES = 2.0
#: Radii below this are treated as noise rather than as a feature.
MIN_FEATURE_RADIUS_MM = 0.3
MAX_FEATURE_RADIUS_MM = 40.0
#: Clearance-hole radius range that is typically a fastener/mounting feature.
MOUNTING_RADIUS_RANGE_MM = (1.2, 8.0)
#: Section area drop, relative to the local median, that counts as a neck.
NARROW_SECTION_RATIO = 0.55
#: Relative change of section area between neighbouring cuts that counts as an
#: abrupt transition.
ABRUPT_TRANSITION_RATIO = 0.45


@dataclass
class ThicknessSample:
    points: np.ndarray
    thickness: np.ndarray
    face_ids: np.ndarray


def sample_thickness(
    mesh: trimesh.Trimesh, max_samples: int = 4000
) -> ThicknessSample | None:
    """Local wall thickness measured by ray casting.

    From the centre of each sampled face a ray is cast along the inward
    normal; the distance to the next surface is the local wall thickness.
    This is the standard ray-based thickness measure. It under-reports on
    strongly curved walls and is undefined where the inward ray escapes the
    solid, which is why samples with no hit are dropped rather than guessed.
    """
    face_count = len(mesh.faces)
    if face_count == 0:
        return None
    if face_count > max_samples:
        rng = np.random.default_rng(seed=12345)
        face_ids = rng.choice(face_count, size=max_samples, replace=False)
    else:
        face_ids = np.arange(face_count)

    points = np.asarray(mesh.triangles_center, dtype=float)[face_ids]
    normals = np.asarray(mesh.face_normals, dtype=float)[face_ids]

    try:
        thickness = trimesh.proximity.thickness(
            mesh=mesh, points=points, exterior=False, normals=normals, method="ray"
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("thickness sampling failed", extra={"reason": str(exc)})
        return None

    thickness = np.asarray(thickness, dtype=float)
    valid = np.isfinite(thickness) & (thickness > 1e-6)
    if not np.any(valid):
        return None
    return ThicknessSample(points[valid], thickness[valid], face_ids[valid])


def detect_thin_walls(
    mesh: trimesh.Trimesh, nozzle_diameter_mm: float, samples: ThicknessSample | None = None
) -> tuple[list[CriticalRegion], float | None]:
    """Regions thinner than two extrusion lines of the configured nozzle.

    The threshold is printer dependent: with a 0.4 mm nozzle a wall below
    0.8 mm cannot be printed as two perimeters, and a 0.35 mm wall cannot be
    printed as a wall at all.
    """
    if samples is None:
        samples = sample_thickness(mesh)
    if samples is None:
        return [], None

    threshold = MIN_WALL_LINES * nozzle_diameter_mm
    critical = samples.thickness < nozzle_diameter_mm
    thin = (samples.thickness < threshold) & ~critical

    regions: list[CriticalRegion] = []
    for mask, severity, label in (
        (critical, "critical", "Below one extrusion width"),
        (thin, "warning", "Below two extrusion widths"),
    ):
        if not np.any(mask):
            continue
        points = samples.points[mask]
        values = samples.thickness[mask]
        clusters = _cluster_points(points, radius=max(2.0 * nozzle_diameter_mm, 1.0))
        for index, cluster in enumerate(clusters[:12]):
            cluster_points = points[cluster]
            cluster_values = values[cluster]
            regions.append(
                CriticalRegion(
                    id=f"thin-{severity}-{index}",
                    kind="thin_wall",
                    label=f"Thin wall - {label.lower()}",
                    severity=severity,
                    description=(
                        f"Minimum measured wall thickness {cluster_values.min():.2f} mm over "
                        f"{len(cluster)} sampled points. With a {nozzle_diameter_mm:.1f} mm nozzle "
                        f"a wall needs at least {threshold:.2f} mm for two perimeters"
                        + (
                            "; below one extrusion width the slicer cannot produce a wall here."
                            if severity == "critical"
                            else "."
                        )
                    ),
                    face_ids=[int(f) for f in samples.face_ids[mask][cluster][:200]],
                    position=[float(v) for v in cluster_points.mean(axis=0)],
                    measurement_mm=float(cluster_values.min()),
                    metric={
                        "threshold_mm": threshold,
                        "nozzle_mm": nozzle_diameter_mm,
                        "sample_count": int(len(cluster)),
                    },
                )
            )
    return regions, float(samples.thickness.min())


def vertex_concavity(mesh: trimesh.Trimesh) -> np.ndarray:
    """Per-vertex normal curvature estimate (positive where concave).

    For neighbouring vertices ``p_j`` of ``p_i`` with outward normal ``n_i``,

        k_i = mean_j ( 2 * n_i . (p_j - p_i) / |p_j - p_i|^2 )

    which is the standard discrete estimate of the normal curvature of the
    surface at ``p_i``. A cylindrical hole of radius ``r`` gives ``k ~ 1/r``.
    """
    vertices = np.asarray(mesh.vertices, dtype=float)
    normals = np.asarray(mesh.vertex_normals, dtype=float)
    edges = np.asarray(mesh.edges_unique, dtype=int)
    if len(edges) == 0:
        return np.zeros(len(vertices))

    a, b = edges[:, 0], edges[:, 1]
    delta = vertices[b] - vertices[a]
    squared = np.einsum("ij,ij->i", delta, delta)
    squared = np.where(squared < 1e-12, 1e-12, squared)

    k_ab = 2.0 * np.einsum("ij,ij->i", normals[a], delta) / squared
    k_ba = -2.0 * np.einsum("ij,ij->i", normals[b], delta) / squared

    total = np.zeros(len(vertices))
    count = np.zeros(len(vertices))
    np.add.at(total, a, k_ab)
    np.add.at(total, b, k_ba)
    np.add.at(count, a, 1.0)
    np.add.at(count, b, 1.0)
    count = np.where(count < 1, 1.0, count)
    return total / count


def detect_concave_features(mesh: trimesh.Trimesh) -> list[CriticalRegion]:
    """Holes and internal radii, found from discrete curvature.

    Concave vertex clusters are grouped by mesh connectivity and the radius is
    estimated as ``1 / k``. ESTIMATE: the radius is derived from discrete
    curvature and is accurate only for reasonably tessellated cylinders.
    """
    curvature = vertex_concavity(mesh)
    magnitude = np.abs(curvature)
    radius = np.where(magnitude > 1e-9, 1.0 / np.where(magnitude > 1e-9, magnitude, 1.0), np.inf)
    concave = (
        (curvature > 0)
        & (radius >= MIN_FEATURE_RADIUS_MM)
        & (radius <= MAX_FEATURE_RADIUS_MM)
    )
    if not np.any(concave):
        return []

    labels = _cluster_vertices(mesh, concave)
    vertices = np.asarray(mesh.vertices, dtype=float)
    regions: list[CriticalRegion] = []

    for index, cluster in enumerate(labels[:20]):
        if len(cluster) < 6:
            continue
        estimated_radius = float(np.median(radius[cluster]))
        centre = vertices[cluster].mean(axis=0)
        extent = float(np.linalg.norm(vertices[cluster].max(axis=0) - vertices[cluster].min(axis=0)))
        is_mounting = (
            MOUNTING_RADIUS_RANGE_MM[0] <= estimated_radius <= MOUNTING_RADIUS_RANGE_MM[1]
            and extent > estimated_radius
        )
        regions.append(
            CriticalRegion(
                id=f"concave-{index}",
                kind="mounting_area" if is_mounting else "hole",
                label=(
                    f"Mounting feature, estimated radius {estimated_radius:.1f} mm"
                    if is_mounting
                    else f"Internal concave feature, estimated radius {estimated_radius:.1f} mm"
                ),
                severity="warning" if estimated_radius < 2.0 else "info",
                description=(
                    f"Concave region with an estimated radius of {estimated_radius:.2f} mm "
                    f"({len(cluster)} vertices). Small internal radii raise the local stress in a "
                    "way this tool does not resolve; a finite element analysis is needed to "
                    "quantify it."
                    + (
                        " Fastener clearance holes are the usual place where a bracket fails, "
                        "so extra walls around it are normally worthwhile."
                        if is_mounting
                        else ""
                    )
                ),
                position=[float(v) for v in centre],
                measurement_mm=estimated_radius,
                metric={"vertex_count": int(len(cluster)), "extent_mm": extent},
            )
        )
    return regions


def detect_overhang_regions(
    prepared: PreparedMesh, overhang_mask: np.ndarray, threshold_deg: float, limit: int = 10
) -> list[CriticalRegion]:
    """Connected clusters of faces that need support."""
    if not np.any(overhang_mask):
        return []
    mesh = prepared.mesh
    indices = np.flatnonzero(overhang_mask)
    lookup = {int(face): i for i, face in enumerate(indices)}

    adjacency = np.asarray(mesh.face_adjacency, dtype=int)
    if len(adjacency):
        keep = overhang_mask[adjacency[:, 0]] & overhang_mask[adjacency[:, 1]]
        pairs = adjacency[keep]
    else:
        pairs = np.zeros((0, 2), dtype=int)

    if len(pairs):
        rows = np.array([lookup[int(p)] for p in pairs[:, 0]])
        cols = np.array([lookup[int(p)] for p in pairs[:, 1]])
        graph = coo_matrix(
            (np.ones(len(rows)), (rows, cols)), shape=(len(indices), len(indices))
        )
        count, labels = connected_components(graph, directed=False)
    else:
        count, labels = len(indices), np.arange(len(indices))

    areas = prepared.face_areas
    regions: list[CriticalRegion] = []
    order = sorted(
        range(count), key=lambda label: float(areas[indices[labels == label]].sum()), reverse=True
    )
    for rank, label in enumerate(order[:limit]):
        members = indices[labels == label]
        area = float(areas[members].sum())
        if area < 1.0:
            continue
        centre = prepared.face_centers[members].mean(axis=0)
        regions.append(
            CriticalRegion(
                id=f"overhang-{rank}",
                kind="overhang",
                label=f"Unsupported overhang, {area:.0f} mm^2",
                severity="warning" if area > 100 else "info",
                description=(
                    f"{len(members)} faces covering {area:.0f} mm^2 fall below the "
                    f"{threshold_deg:.0f} deg overhang threshold in the selected orientation and "
                    "need support material."
                ),
                face_ids=[int(f) for f in members[:500]],
                position=[float(v) for v in centre],
                measurement_mm=None,
                metric={"area_mm2": area, "face_count": int(len(members))},
            )
        )
    return regions


def section_profile(
    mesh: trimesh.Trimesh, axis: np.ndarray, samples: int = 40
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cross-section area along an axis.

    Returns ``(positions, areas, centroids)`` for the cuts that produced a
    valid section.
    """
    axis = np.asarray(axis, dtype=float)
    axis = axis / max(np.linalg.norm(axis), 1e-12)
    projections = np.asarray(mesh.vertices, dtype=float) @ axis
    low, high = float(projections.min()), float(projections.max())
    span = high - low
    if span < 1e-6:
        return np.zeros(0), np.zeros(0), np.zeros((0, 3))

    positions: list[float] = []
    areas: list[float] = []
    centroids: list[np.ndarray] = []
    for fraction in np.linspace(0.02, 0.98, samples):
        offset = low + span * float(fraction)
        section = section_at(mesh, axis * offset, axis)
        if section is None or section.area <= 1e-9:
            continue
        positions.append(offset)
        areas.append(section.area)
        centroids.append(section.centroid_3d)
    return np.asarray(positions), np.asarray(areas), np.asarray(centroids) if centroids else np.zeros((0, 3))


def detect_narrow_sections(
    mesh: trimesh.Trimesh, axis: np.ndarray, samples: int = 40
) -> list[CriticalRegion]:
    """Local minima and abrupt steps in the cross-section area."""
    positions, areas, centroids = section_profile(mesh, axis, samples)
    if len(areas) < 5:
        return []

    median = float(np.median(areas))
    regions: list[CriticalRegion] = []

    for index in range(1, len(areas) - 1):
        area = float(areas[index])
        if area < areas[index - 1] and area < areas[index + 1] and area < median * NARROW_SECTION_RATIO:
            regions.append(
                CriticalRegion(
                    id=f"neck-{index}",
                    kind="narrow_section",
                    label=f"Narrow cross-section, {area:.0f} mm^2",
                    severity="warning",
                    description=(
                        f"The cross-section drops to {area:.0f} mm^2, "
                        f"{area / median * 100:.0f} % of the median section of "
                        f"{median:.0f} mm^2. Load carried along this axis concentrates here."
                    ),
                    position=[float(v) for v in centroids[index]],
                    measurement_mm=None,
                    metric={"area_mm2": area, "median_area_mm2": median},
                )
            )

    for index in range(1, len(areas)):
        previous, current = float(areas[index - 1]), float(areas[index])
        reference = max(previous, current)
        if reference <= 0:
            continue
        change = abs(current - previous) / reference
        if change > ABRUPT_TRANSITION_RATIO:
            regions.append(
                CriticalRegion(
                    id=f"transition-{index}",
                    kind="weak_transition",
                    label=f"Abrupt change of cross-section ({change * 100:.0f} %)",
                    severity="info",
                    description=(
                        f"The cross-section changes from {previous:.0f} mm^2 to {current:.0f} mm^2 "
                        "between neighbouring cuts. Sharp transitions raise the local stress; the "
                        "magnitude is not resolved without a finite element analysis."
                    ),
                    position=[float(v) for v in centroids[index]],
                    measurement_mm=None,
                    metric={"area_before_mm2": previous, "area_after_mm2": current},
                )
            )

    # Keep the report readable: the worst few of each kind.
    regions.sort(key=lambda region: region.metric.get("area_mm2", float("inf")))
    return regions[:8]


# --------------------------------------------------------------------------
# clustering helpers
# --------------------------------------------------------------------------
def _cluster_points(points: np.ndarray, radius: float) -> list[np.ndarray]:
    """Grid clustering: cheap, deterministic and good enough for grouping
    sample points that belong to the same feature."""
    if len(points) == 0:
        return []
    cell = max(radius, 1e-3)
    keys = np.floor(points / cell).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    clusters = [np.flatnonzero(inverse == index) for index in range(len(unique))]
    clusters.sort(key=len, reverse=True)
    return clusters


def _cluster_vertices(mesh: trimesh.Trimesh, mask: np.ndarray) -> list[np.ndarray]:
    """Connected components of the selected vertices over the mesh edges."""
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return []
    lookup = -np.ones(len(mesh.vertices), dtype=np.int64)
    lookup[indices] = np.arange(len(indices))

    edges = np.asarray(mesh.edges_unique, dtype=int)
    keep = mask[edges[:, 0]] & mask[edges[:, 1]]
    pairs = edges[keep]
    if len(pairs) == 0:
        return [np.array([i]) for i in range(len(indices))]

    rows = lookup[pairs[:, 0]]
    cols = lookup[pairs[:, 1]]
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(indices), len(indices)))
    count, labels = connected_components(graph, directed=False)
    clusters = [indices[labels == label] for label in range(count)]
    clusters.sort(key=len, reverse=True)
    return clusters
