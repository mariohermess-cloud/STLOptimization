"""Printability metrics for one build direction.

Every function here takes the build direction ``d`` expressed **in the part
frame** and the precomputed mesh arrays. All formulas and all constants are
documented in ``docs/physics.md``; the constants are gathered at the top of
this module so there is exactly one place to change them.

Nothing in this module is a measurement. The overhang and bed-contact
calculations are exact geometry; support volume, warping risk and print time
are explicitly labelled approximations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from app.geometry.prepared import PreparedMesh, orthonormal_basis
from app.geometry.transforms import minimum_footprint_z_rotation

# --- documented constants -------------------------------------------------
#: Typical sparse-support infill fraction used by slicers. Converts the swept
#: volume under an overhang into an estimate of support material.
SUPPORT_INFILL_FRACTION = 0.15
#: Reference bed contact patch considered sufficient adhesion for a part of
#: 50 mm height. Scaled linearly with part height. HEURISTIC.
ADHESION_REFERENCE_AREA_MM2 = 100.0
ADHESION_REFERENCE_HEIGHT_MM = 50.0
#: Tipping angle at which a part is considered fully stable on the plate.
STABILITY_FULL_TIPPING_ANGLE_DEG = 45.0
#: Bed span at which the warping span factor saturates. HEURISTIC.
WARP_REFERENCE_SPAN_MM = 150.0
#: Multiplier applied to the warping index inside a heated, enclosed chamber.
WARP_ENCLOSURE_FACTOR = 0.55
WARP_LEVEL_THRESHOLDS = (0.25, 0.55)  # < LOW, < MEDIUM, else HIGH
#: Fraction of the solid volume actually extruded for a typical part
#: (walls + top/bottom + sparse infill). Used only by the print-time estimate,
#: which runs before the print settings are chosen.
NOMINAL_MATERIAL_FRACTION = 0.45
#: Fraction of the material's peak volumetric flow a real print sustains.
FLOW_EFFICIENCY = 0.60
#: Fixed overhead per layer (layer change, travel, acceleration). seconds.
LAYER_OVERHEAD_S = 1.5


@dataclass
class OverhangResult:
    overhang_area_mm2: float
    overhang_area_fraction: float
    support_volume_mm3: float
    support_contact_area_mm2: float
    steepest_overhang_deg: float
    threshold_deg: float
    overhang_face_mask: np.ndarray
    bed_face_mask: np.ndarray
    score: float
    support_score: float


@dataclass
class BedResult:
    contact_area_mm2: float
    footprint_area_mm2: float
    footprint_size_mm: tuple[float, float]
    footprint_points: np.ndarray
    max_span_mm: float
    height_mm: float
    com_height_mm: float
    com_margin_mm: float
    com_inside: bool
    slenderness: float
    z_rotation_deg: float
    score: float


@dataclass
class WarpResult:
    risk_index: float
    level: str
    max_bed_span_mm: float
    drivers: list[str]
    score: float


@dataclass
class TimeResult:
    minutes: float
    layer_count: int
    extruded_volume_mm3: float
    support_volume_mm3: float


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(min(high, max(low, value)))


def analyse_overhangs(
    prepared: PreparedMesh,
    build_dir: np.ndarray,
    *,
    threshold_deg: float,
    bed_tolerance_mm: float,
) -> OverhangResult:
    """Exact overhang geometry for a build direction.

    For face normal ``n``, ``phi = arccos(-n . d)`` is the inclination of the
    face measured from the build plate: ``phi = 0`` is a horizontal
    down-facing surface, ``phi = 90`` is a vertical wall. A down-facing face
    needs support when ``phi < threshold``. Faces resting on the plate are
    excluded.

    The support **volume** is an APPROXIMATION: the projected area of each
    unsupported face is swept down to the build plate and multiplied by a
    typical sparse support infill fraction. It ignores that a lower part of
    the model may already carry the support.
    """
    d = np.asarray(build_dir, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)

    cos_to_build = prepared.face_normals @ d
    heights = prepared.face_centers @ d
    vertex_heights = prepared.vertices @ d
    h_min = float(vertex_heights.min())

    down_facing = cos_to_build < 0.0
    phi = np.degrees(np.arccos(np.clip(-cos_to_build, -1.0, 1.0)))

    # A face rests on the plate when its centroid is within tolerance of the
    # lowest point and it faces down.
    on_bed = down_facing & (heights <= h_min + max(bed_tolerance_mm, 1e-6))

    needs_support = down_facing & (phi < float(threshold_deg)) & ~on_bed
    areas = prepared.face_areas

    overhang_area = float(areas[needs_support].sum())
    total_area = float(areas.sum()) or 1.0

    projected = areas * np.abs(cos_to_build)
    drop_height = np.clip(heights - h_min, 0.0, None)
    support_volume = float(
        (projected[needs_support] * drop_height[needs_support]).sum() * SUPPORT_INFILL_FRACTION
    )
    support_contact = float(projected[needs_support].sum())

    steepest = float(phi[needs_support].min()) if np.any(needs_support) else 90.0

    # Overhang score: the fraction of the surface that is self-supporting,
    # weighted by how far each unsupported face is past the threshold.
    if np.any(needs_support):
        severity = (float(threshold_deg) - phi[needs_support]) / max(float(threshold_deg), 1e-6)
        weighted = float((areas[needs_support] * severity).sum())
        overhang_score = 100.0 * _clamp(1.0 - weighted / total_area)
    else:
        overhang_score = 100.0

    # Support score: support material relative to the part itself.
    part_volume = max(prepared.volume_mm3, 1e-6)
    support_score = 100.0 * float(np.exp(-2.0 * support_volume / part_volume))

    return OverhangResult(
        overhang_area_mm2=overhang_area,
        overhang_area_fraction=overhang_area / total_area,
        support_volume_mm3=support_volume,
        support_contact_area_mm2=support_contact,
        steepest_overhang_deg=steepest,
        threshold_deg=float(threshold_deg),
        overhang_face_mask=needs_support,
        bed_face_mask=on_bed,
        score=overhang_score,
        support_score=support_score,
    )


def analyse_bed(
    prepared: PreparedMesh,
    build_dir: np.ndarray,
    *,
    bed_tolerance_mm: float,
    overhangs: OverhangResult,
) -> BedResult:
    """Bed contact, footprint and static stability.

    Stability combines two independent physical criteria:

    * **Tipping.** The part tips about a footprint edge when the centre of
      mass passes over it. ``atan(margin / com_height)`` is the tilt the part
      tolerates before that happens; it saturates at 45 degrees.
    * **Adhesion.** The first layer has to hold the part down. The contact
      area is compared with a reference patch that grows with part height,
      because a taller part applies more leverage to the same patch.

    The two are averaged. A large flat face is therefore *not* automatically
    preferred - it only scores well on the adhesion half.
    """
    d = np.asarray(build_dir, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    u, v = orthonormal_basis(d)

    vertex_heights = prepared.vertices @ d
    h_min = float(vertex_heights.min())
    h_max = float(vertex_heights.max())
    height = h_max - h_min

    contact_area = float(prepared.face_areas[overhangs.bed_face_mask].sum())

    # Footprint outline: the projection of the convex hull bounds the part
    # exactly, and is far cheaper than projecting every vertex.
    hull_2d = np.column_stack((prepared.hull_points @ u, prepared.hull_points @ v))
    z_rotation, footprint_area_box, footprint_size = minimum_footprint_z_rotation(hull_2d)

    # Contact polygon: the vertices that actually touch the plate.
    touching = prepared.vertices[vertex_heights <= h_min + max(bed_tolerance_mm, 1e-6)]
    if len(touching) >= 3:
        contact_2d = np.column_stack((touching @ u, touching @ v))
    else:
        contact_2d = hull_2d

    footprint_area, footprint_points = _hull_area(contact_2d)
    if footprint_area <= 1e-9:
        footprint_area = max(contact_area, 1e-6)
        footprint_points = contact_2d

    com = prepared.center_of_mass
    com_2d = np.array([float(com @ u), float(com @ v)])
    com_height = float(com @ d) - h_min
    margin, inside = _distance_to_hull(footprint_points, com_2d)

    spans = footprint_points.max(axis=0) - footprint_points.min(axis=0) if len(footprint_points) else np.zeros(2)
    max_span = float(np.linalg.norm(spans))

    tipping_angle = np.degrees(np.arctan2(max(margin, 0.0), max(com_height, 1e-6)))
    tipping_score = 100.0 * _clamp(tipping_angle / STABILITY_FULL_TIPPING_ANGLE_DEG)
    if not inside:
        tipping_score = 0.0

    reference_area = ADHESION_REFERENCE_AREA_MM2 * max(1.0, height / ADHESION_REFERENCE_HEIGHT_MM)
    adhesion_score = 100.0 * _clamp(contact_area / reference_area)

    slenderness = height / max(np.sqrt(max(footprint_area, 1e-9)), 1e-9)

    return BedResult(
        contact_area_mm2=contact_area,
        footprint_area_mm2=float(footprint_area),
        footprint_size_mm=(float(footprint_size[0]), float(footprint_size[1])),
        footprint_points=footprint_points,
        max_span_mm=max_span,
        height_mm=height,
        com_height_mm=com_height,
        com_margin_mm=float(margin if inside else -margin),
        com_inside=bool(inside),
        slenderness=float(slenderness),
        z_rotation_deg=float(z_rotation),
        score=float(0.5 * tipping_score + 0.5 * adhesion_score),
    )


def analyse_warping(
    bed: BedResult,
    *,
    warp_tendency: float | None,
    enclosed: bool,
    material_name: str,
) -> WarpResult:
    """Heuristic warping risk.

    ``W = t_material * S * C * E`` with

    * ``t_material`` the material warping index (0 = none, 1 = severe),
    * ``S = min(1, span / 150 mm)`` - longer bonded spans accumulate more
      thermal contraction before the first layer can resist it,
    * ``C = 0.5 + 0.5 * (contact area / footprint area)`` - a large solid first
      layer concentrates the contraction force at its corners,
    * ``E = 0.55`` inside a heated enclosure, otherwise ``1.0``.

    HEURISTIC. There is no thermal simulation behind this. It orders
    orientations by risk; it does not predict whether a part will lift.
    """
    drivers: list[str] = []
    tendency = 0.4 if warp_tendency is None else float(warp_tendency)
    if warp_tendency is None:
        drivers.append("material warping index unknown - a neutral value of 0.4 was assumed")

    span_factor = _clamp(bed.max_span_mm / WARP_REFERENCE_SPAN_MM)
    contact_ratio = _clamp(bed.contact_area_mm2 / max(bed.footprint_area_mm2, 1e-6))
    contact_factor = 0.5 + 0.5 * contact_ratio
    enclosure_factor = WARP_ENCLOSURE_FACTOR if enclosed else 1.0

    risk = _clamp(tendency * span_factor * contact_factor * enclosure_factor)

    if tendency >= 0.6:
        drivers.append(f"{material_name} has a high shrinkage tendency")
    if bed.max_span_mm >= WARP_REFERENCE_SPAN_MM * 0.8:
        drivers.append(f"long bonded span on the plate ({bed.max_span_mm:.0f} mm)")
    if contact_ratio > 0.7:
        drivers.append("large solid first layer")
    if enclosed and tendency >= 0.5:
        drivers.append("enclosed chamber reduces the risk")

    if risk < WARP_LEVEL_THRESHOLDS[0]:
        level = "LOW"
    elif risk < WARP_LEVEL_THRESHOLDS[1]:
        level = "MEDIUM"
    else:
        level = "HIGH"

    return WarpResult(
        risk_index=risk,
        level=level,
        max_bed_span_mm=bed.max_span_mm,
        drivers=drivers,
        score=100.0 * (1.0 - risk),
    )


def estimate_print_time(
    prepared: PreparedMesh,
    bed: BedResult,
    overhangs: OverhangResult,
    *,
    layer_height_mm: float,
    max_volumetric_flow: float | None,
) -> TimeResult:
    """Flow-limited print time estimate. APPROXIMATION.

    ``T = V_extruded / (flow * efficiency) + layers * overhead``

    The extruded volume is the part volume at a nominal material fraction plus
    the estimated support volume. This is a comparative estimate for ranking
    orientations; it is not a slicer and will not match a sliced G-code time.
    """
    flow = float(max_volumetric_flow) if max_volumetric_flow else 12.0
    effective_flow = max(flow * FLOW_EFFICIENCY, 1e-3)

    extruded = prepared.volume_mm3 * NOMINAL_MATERIAL_FRACTION + overhangs.support_volume_mm3
    layers = int(max(1, round(bed.height_mm / max(layer_height_mm, 1e-3))))
    seconds = extruded / effective_flow + layers * LAYER_OVERHEAD_S

    return TimeResult(
        minutes=seconds / 60.0,
        layer_count=layers,
        extruded_volume_mm3=float(extruded),
        support_volume_mm3=float(overhangs.support_volume_mm3),
    )


def material_score(prepared: PreparedMesh, overhangs: OverhangResult) -> float:
    """Share of extruded material that ends up in the part rather than in
    support structures."""
    part = max(prepared.volume_mm3 * NOMINAL_MATERIAL_FRACTION, 1e-6)
    return 100.0 * float(part / (part + overhangs.support_volume_mm3))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _hull_area(points_2d: np.ndarray) -> tuple[float, np.ndarray]:
    points = np.asarray(points_2d, dtype=float)
    if len(points) < 3:
        return 0.0, points
    try:
        hull = ConvexHull(points)
        return float(hull.volume), points[hull.vertices]  # 'volume' is area in 2D
    except (QhullError, ValueError):
        return 0.0, points


def _distance_to_hull(hull_points: np.ndarray, point: np.ndarray) -> tuple[float, bool]:
    """Signed distance from ``point`` to the boundary of a convex polygon.

    Returns ``(distance, inside)``; the distance is always non-negative and
    ``inside`` says which side of the boundary the point is on.
    """
    polygon = np.asarray(hull_points, dtype=float)
    if len(polygon) < 3:
        return 0.0, False

    edges = np.roll(polygon, -1, axis=0) - polygon
    to_point = point[None, :] - polygon
    lengths = np.linalg.norm(edges, axis=1)
    lengths = np.where(lengths < 1e-12, 1e-12, lengths)
    t = np.clip(np.sum(to_point * edges, axis=1) / lengths**2, 0.0, 1.0)
    closest = polygon + edges * t[:, None]
    distance = float(np.min(np.linalg.norm(point[None, :] - closest, axis=1)))

    cross = edges[:, 0] * to_point[:, 1] - edges[:, 1] * to_point[:, 0]
    inside = bool(np.all(cross >= -1e-9) or np.all(cross <= 1e-9))
    return distance, inside
