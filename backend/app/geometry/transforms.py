"""Rotation helpers shared by the orientation engine.

Conventions used throughout the application
-------------------------------------------
* World frame: ``+Z`` is the build direction (the direction in which layers are
  stacked), the ``XY`` plane is the layer plane and the build plate.
* An *orientation* is a rotation matrix ``R`` applied to the part. A point
  ``p`` of the uploaded model ends up at ``R @ p`` on the build plate.
* Consequently the build direction expressed **in the part frame** is
  ``d = R.T @ [0, 0, 1]``. Every anisotropy and overhang calculation is done
  with ``d`` in the part frame, which avoids rotating the mesh itself.
* Reported Euler angles are intrinsic ``X -> Y -> Z`` ("xyz" in SciPy terms),
  in degrees, matching the rotation fields of the export schema.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy.spatial.transform import Rotation

Z_AXIS = np.array([0.0, 0.0, 1.0])


def rotation_from_euler_xyz(rx: float, ry: float, rz: float) -> np.ndarray:
    """Rotation matrix from intrinsic X-Y-Z Euler angles given in degrees."""
    return Rotation.from_euler("xyz", [rx, ry, rz], degrees=True).as_matrix()


def euler_xyz_from_rotation(matrix: np.ndarray) -> tuple[float, float, float]:
    """Intrinsic X-Y-Z Euler angles in degrees for a rotation matrix."""
    with warnings.catch_warnings():
        # Gimbal lock is expected and harmless here: when it happens the third
        # angle is redundant, and any of the equivalent triples describes the
        # same orientation.
        warnings.simplefilter("ignore", UserWarning)
        rx, ry, rz = Rotation.from_matrix(matrix).as_euler("xyz", degrees=True)
    return float(rx), float(ry), float(rz)


def build_direction_in_part_frame(matrix: np.ndarray) -> np.ndarray:
    """``d`` - the build direction as seen from the unrotated part."""
    return np.asarray(matrix, dtype=float).T @ Z_AXIS


def rotation_aligning_vector_to_z(direction: np.ndarray) -> np.ndarray:
    """Smallest rotation that maps ``direction`` (part frame) onto world ``+Z``.

    This is the rotation that places the part such that ``direction`` points
    "up" along the build axis. The remaining degree of freedom (spin about Z)
    is resolved separately by :func:`minimum_footprint_z_rotation`.
    """
    v = np.asarray(direction, dtype=float)
    norm = np.linalg.norm(v)
    if norm < 1e-12:
        return np.eye(3)
    v = v / norm
    axis = np.cross(v, Z_AXIS)
    axis_norm = np.linalg.norm(axis)
    dot = float(np.clip(np.dot(v, Z_AXIS), -1.0, 1.0))
    if axis_norm < 1e-12:
        if dot > 0:
            return np.eye(3)
        # Anti-parallel: rotate 180 deg about any axis orthogonal to Z.
        return Rotation.from_rotvec(np.pi * np.array([1.0, 0.0, 0.0])).as_matrix()
    axis = axis / axis_norm
    angle = float(np.arccos(dot))
    return Rotation.from_rotvec(axis * angle).as_matrix()


def rotation_about_z(angle_deg: float) -> np.ndarray:
    return Rotation.from_euler("z", angle_deg, degrees=True).as_matrix()


def minimum_footprint_z_rotation(points_xy: np.ndarray) -> tuple[float, float, tuple[float, float]]:
    """Rotating-calipers minimum-area bounding rectangle of a 2D point set.

    Returns ``(angle_deg, area, (width, depth))`` where ``angle_deg`` is the
    rotation about ``+Z`` that minimises the axis-aligned bounding box of the
    projected part. This resolves the spin degree of freedom with a criterion
    that actually matters for printing: the smallest bed footprint, which also
    decides whether the part fits inside the build volume.

    The minimum-area rectangle of a convex hull is always flush with one hull
    edge, so only the hull edge directions have to be tested (exact, not a
    sampled approximation).
    """
    pts = np.asarray(points_xy, dtype=float)
    if pts.shape[0] < 3:
        return 0.0, 0.0, (0.0, 0.0)

    from scipy.spatial import ConvexHull, QhullError

    try:
        hull = ConvexHull(pts)
        hull_pts = pts[hull.vertices]
    except (QhullError, ValueError):
        hull_pts = pts

    edges = np.roll(hull_pts, -1, axis=0) - hull_pts
    lengths = np.linalg.norm(edges, axis=1)
    keep = lengths > 1e-9
    if not np.any(keep):
        return 0.0, 0.0, (0.0, 0.0)
    angles = np.arctan2(edges[keep, 1], edges[keep, 0])
    # Directions are equivalent modulo 90 degrees for a rectangle.
    angles = np.unique(np.mod(angles, np.pi / 2.0))

    # Vectorised over all candidate angles at once: for a hull with h points
    # and a candidate angles this is one (a, h) matrix product per axis rather
    # than a Python loop, which matters because a dense mesh can have a hull
    # with thousands of edges.
    cos = np.cos(-angles)
    sin = np.sin(-angles)
    x = np.outer(cos, hull_pts[:, 0]) - np.outer(sin, hull_pts[:, 1])
    y = np.outer(sin, hull_pts[:, 0]) + np.outer(cos, hull_pts[:, 1])
    width = x.max(axis=1) - x.min(axis=1)
    depth = y.max(axis=1) - y.min(axis=1)
    areas = width * depth
    best = int(np.argmin(areas))
    return (
        float(np.degrees(-angles[best])),
        float(areas[best]),
        (float(width[best]), float(depth[best])),
    )
