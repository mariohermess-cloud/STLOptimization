"""Cross-section properties of a mesh cut by a plane.

The mechanical solver is a *section* analysis: it cuts the part along the load
path and evaluates classical beam stresses on the real cross-sections of the
model. This module turns a cutting plane into the section properties that
beam theory needs (area, centroid, second moments of area, polar moment,
extreme fibre distance, perimeter) plus the shell/core split used by the
wall-versus-infill recommendation.

All polygon integrals are evaluated analytically from the section outline.
Interior rings (holes) are handled with the correct sign, so a hollow section
reports the hollow properties rather than the solid envelope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import trimesh

logger = logging.getLogger(__name__)


def second_moment_about_axis(inertia: np.ndarray, axis_2d: np.ndarray) -> float:
    """Second moment of area about an in-plane axis through the centroid.

    ``inertia`` is ``[[Ixx, Ixy], [Ixy, Iyy]]`` with ``Ixx = integral y^2 dA``
    and ``Iyy = integral x^2 dA``. For a neutral axis along ``a`` the distance
    of a point from that axis is ``p . n`` with ``n`` the in-plane normal of
    ``a``, so

        I = integral (n_x x + n_y y)^2 dA
          = n_x^2 * Iyy + 2 n_x n_y * Ixy + n_y^2 * Ixx

    Note that ``n_x`` pairs with ``Iyy``, not with ``Ixx``.
    """
    a = np.asarray(axis_2d, dtype=float)
    norm = np.linalg.norm(a)
    if norm < 1e-12:
        return float(max(inertia[0, 0], inertia[1, 1]))
    a = a / norm
    n = np.array([-a[1], a[0]])
    return float(
        n[0] * n[0] * inertia[1, 1] + 2.0 * n[0] * n[1] * inertia[0, 1] + n[1] * n[1] * inertia[0, 0]
    )


@dataclass
class SectionProperties:
    """Properties of one planar cut, expressed in the section's 2D frame.

    ``to_3d`` maps a homogeneous 2D point of the section frame into part
    coordinates, so the extreme fibre can be located in 3D.
    """

    origin: np.ndarray  # cut plane origin in part coordinates
    normal: np.ndarray  # cut plane normal (unit) in part coordinates
    area: float
    centroid_2d: np.ndarray
    centroid_3d: np.ndarray
    #: Second moments of area about the section centroid: [[Ixx, Ixy], [Ixy, Iyy]]
    inertia: np.ndarray
    polar_moment: float
    perimeter: float
    max_radius: float
    #: 2D offsets (from the centroid) of the point furthest from the centroid.
    extreme_point_2d: np.ndarray
    to_3d: np.ndarray
    polygon_count: int
    hole_count: int

    def axis_2d_to_3d(self, vector_2d: np.ndarray) -> np.ndarray:
        """Map a direction of the section frame into part coordinates."""
        direction = self.to_3d[:3, :2] @ np.asarray(vector_2d, dtype=float)
        norm = np.linalg.norm(direction)
        return direction / norm if norm > 1e-12 else direction

    def second_moment_about(self, axis_2d: np.ndarray) -> float:
        """Second moment of area for bending about ``axis_2d`` (in-plane axis).

        For a bending moment whose vector points along ``axis_2d`` the neutral
        axis is ``axis_2d`` itself; see :func:`second_moment_about_axis`.
        """
        return second_moment_about_axis(self.inertia, axis_2d)

    def extreme_fibre_distance(self, axis_2d: np.ndarray) -> tuple[float, np.ndarray]:
        """Distance of the furthest material point from the neutral axis.

        Returns ``(c, direction)`` where ``direction`` is the in-plane unit
        vector pointing at the extreme fibre.
        """
        a = np.asarray(axis_2d, dtype=float)
        norm = np.linalg.norm(a)
        if norm < 1e-12:
            return self.max_radius, np.array([1.0, 0.0])
        a = a / norm
        n = np.array([-a[1], a[0]])
        offset = self.extreme_point_2d
        projection = float(abs(offset @ n))
        if projection < 1e-9:
            return self.max_radius, n
        return projection, n * np.sign(offset @ n)


def _ring_integrals(coords: np.ndarray) -> tuple[float, float, float, float, float, float]:
    """Shoelace integrals of a closed ring.

    Returns ``(A, Qx, Qy, Ixx, Iyy, Ixy)`` about the 2D frame origin, where
    ``Qx = integral of y dA`` and ``Qy = integral of x dA``. A clockwise ring
    (a hole, as produced by shapely) yields negative values, so summing rings
    subtracts holes automatically.
    """
    x = coords[:, 0]
    y = coords[:, 1]
    x1 = np.roll(x, -1)
    y1 = np.roll(y, -1)
    cross = x * y1 - x1 * y

    area = float(cross.sum() / 2.0)
    qy = float(((x + x1) * cross).sum() / 6.0)
    qx = float(((y + y1) * cross).sum() / 6.0)
    ixx = float(((y * y + y * y1 + y1 * y1) * cross).sum() / 12.0)
    iyy = float(((x * x + x * x1 + x1 * x1) * cross).sum() / 12.0)
    ixy = float(((x * y1 + 2 * x * y + 2 * x1 * y1 + x1 * y) * cross).sum() / 24.0)
    return area, qx, qy, ixx, iyy, ixy


def polygon_properties(polygons) -> dict | None:
    """Area, centroid, centroidal inertia and perimeter of shapely polygons."""
    area = qx = qy = ixx = iyy = ixy = 0.0
    perimeter = 0.0
    points: list[np.ndarray] = []
    hole_count = 0

    for polygon in polygons:
        rings = [np.asarray(polygon.exterior.coords, dtype=float)[:-1]]
        perimeter += float(polygon.exterior.length)
        for interior in polygon.interiors:
            rings.append(np.asarray(interior.coords, dtype=float)[:-1])
            perimeter += float(interior.length)
            hole_count += 1
        for ring in rings:
            if len(ring) < 3:
                continue
            a, rqx, rqy, rixx, riyy, rixy = _ring_integrals(ring)
            area += a
            qx += rqx
            qy += rqy
            ixx += rixx
            iyy += riyy
            ixy += rixy
        points.append(np.asarray(polygon.exterior.coords, dtype=float)[:-1])

    if abs(area) < 1e-9 or not points:
        return None

    centroid = np.array([qy / area, qx / area])
    # Parallel axis theorem back to the centroid.
    ixx_c = ixx - area * centroid[1] ** 2
    iyy_c = iyy - area * centroid[0] ** 2
    ixy_c = ixy - area * centroid[0] * centroid[1]

    all_points = np.vstack(points)
    offsets = all_points - centroid
    radii = np.linalg.norm(offsets, axis=1)
    index = int(np.argmax(radii))

    return {
        "area": abs(area),
        "centroid": centroid,
        "inertia": np.array([[abs(ixx_c), ixy_c], [ixy_c, abs(iyy_c)]]),
        "perimeter": perimeter,
        "max_radius": float(radii[index]),
        "extreme_point": offsets[index],
        "polygon_count": len(points),
        "hole_count": hole_count,
    }


def section_at(
    mesh: trimesh.Trimesh, origin: np.ndarray, normal: np.ndarray
) -> SectionProperties | None:
    """Cut ``mesh`` with a plane and return the section properties.

    Returns ``None`` when the plane misses the part or the cut degenerates
    (for example a plane tangent to a surface).
    """
    normal = np.asarray(normal, dtype=float)
    norm = np.linalg.norm(normal)
    if norm < 1e-12:
        return None
    normal = normal / norm

    try:
        path = mesh.section(plane_origin=np.asarray(origin, dtype=float), plane_normal=normal)
        if path is None:
            return None
        planar, to_3d = path.to_2D(normal=normal)
        polygons = planar.polygons_full
    except Exception as exc:  # noqa: BLE001 - section failures are expected on odd meshes
        logger.debug("section failed", extra={"reason": str(exc)})
        return None

    if polygons is None or len(polygons) == 0:
        return None

    props = polygon_properties(polygons)
    if props is None:
        return None

    centroid_2d = props["centroid"]
    centroid_3d = (to_3d @ np.array([centroid_2d[0], centroid_2d[1], 0.0, 1.0]))[:3]

    return SectionProperties(
        origin=np.asarray(origin, dtype=float),
        normal=normal,
        area=props["area"],
        centroid_2d=centroid_2d,
        centroid_3d=centroid_3d,
        inertia=props["inertia"],
        polar_moment=float(props["inertia"][0, 0] + props["inertia"][1, 1]),
        perimeter=props["perimeter"],
        max_radius=props["max_radius"],
        extreme_point_2d=props["extreme_point"],
        to_3d=np.asarray(to_3d, dtype=float),
        polygon_count=props["polygon_count"],
        hole_count=props["hole_count"],
    )


def shell_inertia_fraction(
    mesh: trimesh.Trimesh,
    origin: np.ndarray,
    normal: np.ndarray,
    shell_thickness_mm: float,
    bending_axis_2d: np.ndarray | None = None,
) -> dict | None:
    """How much of the section's bending stiffness the outer walls provide.

    The section outline is offset inwards by ``shell_thickness_mm``; the
    difference between the full section and the remaining core is the material
    that the printed perimeters occupy. Reported as the fraction of the second
    moment of area (bending) and of the plain area (axial) carried by the
    shell. This is the calculation behind the wall-versus-infill
    recommendation - not a rule of thumb.
    """
    normal = np.asarray(normal, dtype=float)
    normal = normal / max(np.linalg.norm(normal), 1e-12)
    try:
        path = mesh.section(plane_origin=np.asarray(origin, dtype=float), plane_normal=normal)
        if path is None:
            return None
        planar, _ = path.to_2D(normal=normal)
        polygons = planar.polygons_full
    except Exception:  # noqa: BLE001
        return None
    if not polygons:
        return None

    full = polygon_properties(polygons)
    if full is None:
        return None

    cores = []
    for polygon in polygons:
        try:
            core = polygon.buffer(-abs(shell_thickness_mm), join_style=2)
        except Exception:  # noqa: BLE001
            core = None
        if core is None or core.is_empty:
            continue
        if core.geom_type == "Polygon":
            cores.append(core)
        elif core.geom_type == "MultiPolygon":
            cores.extend(list(core.geoms))

    core_props = polygon_properties(cores) if cores else None

    axis = np.array([1.0, 0.0]) if bending_axis_2d is None else np.asarray(bending_axis_2d, float)

    def _inertia(props) -> float:
        return second_moment_about_axis(props["inertia"], axis)

    full_i = _inertia(full)
    if core_props is None:
        return {
            "shell_area_fraction": 1.0,
            "shell_inertia_fraction": 1.0,
            "section_area_mm2": full["area"],
            "shell_thickness_mm": abs(shell_thickness_mm),
            "core_is_empty": True,
        }

    # Shift the core inertia to the full-section centroid before subtracting.
    offset = core_props["centroid"] - full["centroid"]
    a = axis / max(np.linalg.norm(axis), 1e-12)
    n = np.array([-a[1], a[0]])
    core_i = _inertia(core_props) + core_props["area"] * float(offset @ n) ** 2

    shell_area = max(full["area"] - core_props["area"], 0.0)
    shell_i = max(full_i - core_i, 0.0)

    return {
        "shell_area_fraction": float(shell_area / full["area"]) if full["area"] > 0 else 1.0,
        "shell_inertia_fraction": float(shell_i / full_i) if full_i > 1e-12 else 1.0,
        "section_area_mm2": float(full["area"]),
        "core_area_mm2": float(core_props["area"]),
        "shell_thickness_mm": abs(shell_thickness_mm),
        "core_is_empty": False,
    }
