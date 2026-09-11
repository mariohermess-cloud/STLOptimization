"""Build-direction candidate generation for the multi-stage search.

An orientation has three degrees of freedom, but the printability and
anisotropy physics depend almost entirely on **two** of them: the build
direction ``d`` in the part frame. The remaining spin about the build axis
does not change overhangs, layer alignment, bed contact or part height - it
only changes the footprint rectangle on the plate. That third degree of
freedom is therefore not searched by brute force; it is solved exactly with a
rotating-calipers minimum-area rectangle (see
:func:`app.geometry.transforms.minimum_footprint_z_rotation`).

Searching a 2-sphere instead of a 3-torus of Euler angles is what keeps the
search affordable: a 15 degree grid over directions is ~250 candidates instead
of ~14000 Euler triples, and it does not skip anything physically distinct.
"""

from __future__ import annotations

import numpy as np
import trimesh


def sphere_grid(step_deg: float) -> np.ndarray:
    """Directions on the unit sphere on a regular angular grid.

    The number of azimuth samples is scaled with ``sin(polar angle)`` so the
    samples stay roughly equidistant instead of bunching up at the poles.
    """
    step = np.radians(max(float(step_deg), 1.0))
    directions: list[np.ndarray] = []
    polar_steps = max(int(round(np.pi / step)), 1)
    for i in range(polar_steps + 1):
        theta = i * np.pi / polar_steps
        sin_theta = np.sin(theta)
        if sin_theta < 1e-6:
            directions.append(np.array([0.0, 0.0, np.cos(theta)]))
            continue
        count = max(int(round(2.0 * np.pi * sin_theta / step)), 1)
        for j in range(count):
            phi = 2.0 * np.pi * j / count
            directions.append(
                np.array([sin_theta * np.cos(phi), sin_theta * np.sin(phi), np.cos(theta)])
            )
    return np.asarray(directions, dtype=float)


def axis_aligned_directions() -> np.ndarray:
    return np.array(
        [
            [0.0, 0.0, 1.0],
            [0.0, 0.0, -1.0],
            [1.0, 0.0, 0.0],
            [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, -1.0, 0.0],
        ]
    )


def flat_face_directions(mesh: trimesh.Trimesh, max_count: int = 24) -> np.ndarray:
    """Directions that rest one of the part's large flat faces on the plate.

    A planar facet with outward normal ``n`` lies flat on the plate when the
    build direction is ``-n``. These orientations are the ones a human would
    try first, and a sampled grid can miss them by several degrees, so they are
    injected explicitly.
    """
    try:
        facets = mesh.facets
        areas = np.asarray(mesh.facets_area, dtype=float)
    except Exception:  # noqa: BLE001
        return np.zeros((0, 3))
    if facets is None or len(facets) == 0:
        return np.zeros((0, 3))

    order = np.argsort(areas)[::-1][:max_count]
    directions = []
    for index in order:
        faces = facets[index]
        if len(faces) == 0:
            continue
        normal = np.asarray(mesh.face_normals[faces[0]], dtype=float)
        norm = np.linalg.norm(normal)
        if norm < 1e-9:
            continue
        directions.append(-normal / norm)
    if not directions:
        return np.zeros((0, 3))
    return np.asarray(directions, dtype=float)


def refine_around(direction: np.ndarray, max_angle_deg: float, step_deg: float) -> np.ndarray:
    """Directions inside a cone around ``direction``.

    Sampled on a polar grid inside the cone so the spacing near the centre
    matches the spacing near the rim.
    """
    d = np.asarray(direction, dtype=float)
    d = d / max(np.linalg.norm(d), 1e-12)
    reference = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(d, reference)
    u /= max(np.linalg.norm(u), 1e-12)
    v = np.cross(d, u)

    step = max(float(step_deg), 0.25)
    max_angle = max(float(max_angle_deg), step)
    directions = [d]
    rings = max(int(round(max_angle / step)), 1)
    for ring in range(1, rings + 1):
        alpha = np.radians(min(ring * step, max_angle))
        count = max(int(round(2.0 * np.pi * np.sin(alpha) / np.radians(step))), 1)
        for j in range(count):
            phi = 2.0 * np.pi * j / count
            offset = np.cos(alpha) * d + np.sin(alpha) * (np.cos(phi) * u + np.sin(phi) * v)
            directions.append(offset / np.linalg.norm(offset))
    return np.asarray(directions, dtype=float)


def deduplicate(directions: np.ndarray, tolerance_deg: float = 2.0) -> np.ndarray:
    """Drop directions that are within ``tolerance_deg`` of one already kept."""
    if len(directions) == 0:
        return directions
    threshold = float(np.cos(np.radians(tolerance_deg)))
    norms = np.linalg.norm(directions, axis=1)
    units = directions[norms > 1e-9] / norms[norms > 1e-9][:, None]
    if len(units) == 0:
        return units

    kept = np.empty((0, 3), dtype=float)
    for unit in units:
        if len(kept) and float((kept @ unit).max()) > threshold:
            continue
        kept = np.vstack((kept, unit))
    return kept
