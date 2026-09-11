"""Face region picking for the 3D viewer.

When the user clicks a face in the viewer the browser only knows one triangle
index. Selecting a *surface* - the whole flat mounting pad, the whole bore -
needs mesh connectivity, so it is done on the server where the adjacency graph
already exists. The returned triangle indices address the same mesh the viewer
loaded (the repaired mesh served by ``/api/models/{id}/mesh.stl``), so the
selection round-trips exactly.
"""

from __future__ import annotations

from collections import deque

import numpy as np
import trimesh


def pick_region(
    mesh: trimesh.Trimesh,
    face_id: int,
    angle_tolerance_deg: float = 20.0,
    max_faces: int = 20000,
) -> dict:
    """Flood fill from ``face_id`` across adjacent faces within an angle.

    A tolerance of 0 selects only exactly coplanar neighbours; the default of
    20 degrees follows gentle curvature such as a fillet or a bore without
    escaping around a sharp edge.
    """
    face_count = len(mesh.faces)
    if not 0 <= face_id < face_count:
        raise IndexError(f"Face {face_id} is outside the mesh (0..{face_count - 1}).")

    normals = np.asarray(mesh.face_normals, dtype=float)
    seed_normal = normals[face_id]
    threshold = float(np.cos(np.radians(max(angle_tolerance_deg, 0.0))))

    adjacency: dict[int, list[int]] = {}
    for a, b in np.asarray(mesh.face_adjacency, dtype=int):
        adjacency.setdefault(int(a), []).append(int(b))
        adjacency.setdefault(int(b), []).append(int(a))

    selected: set[int] = {int(face_id)}
    queue: deque[int] = deque([int(face_id)])
    while queue and len(selected) < max_faces:
        current = queue.popleft()
        for neighbour in adjacency.get(current, ()):
            if neighbour in selected:
                continue
            # Compare against the seed so the region cannot creep around a
            # curve one tolerance step at a time.
            if float(np.dot(normals[neighbour], seed_normal)) < threshold:
                continue
            selected.add(neighbour)
            queue.append(neighbour)

    indices = np.array(sorted(selected), dtype=int)
    areas = np.asarray(mesh.area_faces, dtype=float)[indices]
    centers = np.asarray(mesh.triangles_center, dtype=float)[indices]
    total_area = float(areas.sum())
    centroid = (
        (centers * areas[:, None]).sum(axis=0) / total_area
        if total_area > 1e-12
        else centers.mean(axis=0)
    )
    average_normal = (normals[indices] * areas[:, None]).sum(axis=0)
    norm = np.linalg.norm(average_normal)
    average_normal = average_normal / norm if norm > 1e-12 else seed_normal

    deviation = normals[indices] @ average_normal
    is_planar = bool(np.all(deviation > np.cos(np.radians(1.0))))

    return {
        "face_ids": [int(i) for i in indices],
        "area_mm2": total_area,
        "centroid": [float(v) for v in centroid],
        "normal": [float(v) for v in average_normal],
        "is_planar": is_planar,
    }
