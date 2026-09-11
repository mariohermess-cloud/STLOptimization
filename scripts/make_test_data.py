#!/usr/bin/env python3
"""Generate the deterministic STL fixtures used by the tests and the demos.

Every part is built from primitives so the expected geometry is known exactly,
which is what makes the orientation regression tests meaningful: for a beam
loaded in bending we know which orientations put the bending stress in the
layer plane, so the optimiser's preference can be asserted rather than
eyeballed.

Run:  python scripts/make_test_data.py [output_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh


def cube(size: float = 20.0) -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(size, size, size))


def beam(length: float = 120.0, width: float = 12.0, height: float = 8.0) -> trimesh.Trimesh:
    """A slender beam along X. Bending about Y is the interesting load case."""
    return trimesh.creation.box(extents=(length, width, height))


def cylinder(radius: float = 12.0, height: float = 60.0) -> trimesh.Trimesh:
    return trimesh.creation.cylinder(radius=radius, height=height, sections=64)


def bracket() -> trimesh.Trimesh:
    """L-bracket: a 60x40x6 base plate with a 40x6x50 upright and two M5
    clearance holes in the base."""
    base = trimesh.creation.box(extents=(60, 40, 6))
    base.apply_translation((0, 0, 3))
    upright = trimesh.creation.box(extents=(6, 40, 50))
    upright.apply_translation((-27, 0, 25))
    part = trimesh.boolean.union([base, upright])

    holes = []
    for x in (10.0, 25.0):
        hole = trimesh.creation.cylinder(radius=2.6, height=20, sections=48)
        hole.apply_translation((x, 0, 3))
        holes.append(hole)
    part = trimesh.boolean.difference([part, *holes])
    part.merge_vertices()
    return part


def hook() -> trimesh.Trimesh:
    """A swept hook: a square profile swept along a circular arc plus a shank.

    Useful because the load path curves, so a single global axis is wrong and
    the section sweep has to follow the geometry.
    """
    profile = np.array([[-4, -4], [4, -4], [4, 4], [-4, 4]], dtype=float)
    angles = np.linspace(np.pi * 0.15, np.pi * 1.35, 40)
    radius = 22.0
    path = np.column_stack(
        (radius * np.cos(angles), np.zeros_like(angles), radius * np.sin(angles))
    )
    arc = trimesh.creation.sweep_polygon(
        trimesh.path.polygons.Polygon(profile), path
    )
    shank = trimesh.creation.box(extents=(8, 8, 40))
    shank.apply_translation((radius * np.cos(angles[0]), 0, radius * np.sin(angles[0]) - 20))
    part = trimesh.boolean.union([arc, shank])
    part.merge_vertices()
    return part


def mounting_tab() -> trimesh.Trimesh:
    """A thin tab with a single bolt hole - deliberately close to the printable
    wall limit so the thin-wall detector has something to find."""
    plate = trimesh.creation.box(extents=(40, 20, 1.2))
    boss = trimesh.creation.cylinder(radius=7.0, height=4.0, sections=48)
    boss.apply_translation((-12, 0, 2.0))
    part = trimesh.boolean.union([plate, boss])
    hole = trimesh.creation.cylinder(radius=2.6, height=20, sections=48)
    hole.apply_translation((-12, 0, 0))
    part = trimesh.boolean.difference([part, hole])
    part.merge_vertices()
    return part


def overhang_test() -> trimesh.Trimesh:
    """A block with a large horizontal ceiling and a 30 degree wedge, so one
    orientation is clearly support-free and another clearly is not."""
    body = trimesh.creation.box(extents=(40, 30, 10))
    body.apply_translation((0, 0, 5))
    arm = trimesh.creation.box(extents=(40, 30, 8))
    arm.apply_translation((0, 0, 34))
    column = trimesh.creation.box(extents=(8, 30, 30))
    column.apply_translation((-16, 0, 20))
    part = trimesh.boolean.union([body, arm, column])
    part.merge_vertices()
    return part


def large_performance_part(subdivisions: int = 6) -> trimesh.Trimesh:
    """A dense mesh for the performance benchmark (~1.3 M triangles)."""
    sphere = trimesh.creation.icosphere(subdivisions=subdivisions, radius=40.0)
    return sphere


PARTS = {
    "cube": cube,
    "beam": beam,
    "cylinder": cylinder,
    "bracket": bracket,
    "hook": hook,
    "mounting_tab": mounting_tab,
    "overhang_test": overhang_test,
}


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "test-data")
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, factory in PARTS.items():
        mesh = factory()
        path = out_dir / f"{name}.stl"
        path.write_bytes(mesh.export(file_type="stl"))
        print(f"{path}  {len(mesh.faces):>7,} triangles  watertight={mesh.is_watertight}")

    # An ASCII STL so the loader's format detection is exercised by fixtures.
    ascii_path = out_dir / "cube_ascii.stl"
    ascii_path.write_bytes(cube(10.0).export(file_type="stl_ascii").encode("utf-8"))
    print(f"{ascii_path}  ASCII")

    if "--large" in sys.argv:
        mesh = large_performance_part()
        path = out_dir / "large_sphere.stl"
        path.write_bytes(mesh.export(file_type="stl"))
        print(f"{path}  {len(mesh.faces):>7,} triangles")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
