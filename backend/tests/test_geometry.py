"""Geometry engine tests.

Every assertion is against a quantity that is known analytically for the
fixture, not against a value captured from a previous run.
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from app.core.errors import InvalidMeshError, UnsupportedFileError
from app.geometry.analyzer import compute_metrics, decimate_for_analysis, validate_and_repair
from app.geometry.loader import detect_stl_format, load_stl, sanitize_filename
from app.geometry.regions import pick_region
from app.geometry.sections import section_at, shell_inertia_fraction
from app.geometry.transforms import (
    build_direction_in_part_frame,
    euler_xyz_from_rotation,
    minimum_footprint_z_rotation,
    rotation_aligning_vector_to_z,
    rotation_from_euler_xyz,
)


class TestLoader:
    def test_detects_binary_and_ascii(self, stl_bytes, test_data_dir):
        assert detect_stl_format(stl_bytes("cube")).kind == "binary"
        ascii_data = (test_data_dir / "cube_ascii.stl").read_bytes()
        assert detect_stl_format(ascii_data).kind == "ascii"

    def test_binary_triangle_count_matches_file(self, stl_bytes):
        fmt = detect_stl_format(stl_bytes("bracket"))
        mesh, _ = load_stl(stl_bytes("bracket"))
        assert fmt.declared_triangles == len(mesh.faces)

    def test_rejects_garbage(self):
        with pytest.raises((InvalidMeshError, UnsupportedFileError)):
            load_stl(b"this is definitely not a mesh file at all")

    def test_rejects_truncated_binary(self, stl_bytes):
        data = stl_bytes("bracket")
        with pytest.raises(InvalidMeshError):
            load_stl(data[: len(data) // 2])

    def test_rejects_empty(self):
        with pytest.raises(InvalidMeshError):
            load_stl(b"")

    @pytest.mark.parametrize(
        "name",
        ["../../etc/passwd", "C:\\Windows\\system32\\evil.stl", "a" * 500, "", None],
    )
    def test_filename_is_sanitised(self, name):
        cleaned = sanitize_filename(name)
        assert "/" not in cleaned and "\\" not in cleaned
        assert 0 < len(cleaned) <= 120


class TestValidationAndMetrics:
    def test_cube_metrics_are_exact(self, mesh_of):
        mesh = mesh_of("cube")
        validation = validate_and_repair(mesh)
        metrics = compute_metrics(mesh, validation)

        assert validation.is_watertight
        assert validation.volume_is_reliable
        assert metrics.dimensions == pytest.approx([20.0, 20.0, 20.0])
        assert metrics.volume_mm3 == pytest.approx(8000.0, rel=1e-9)
        assert metrics.surface_area_mm2 == pytest.approx(2400.0, rel=1e-9)
        assert metrics.center_of_mass == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
        assert metrics.aspect_ratio == pytest.approx(1.0)
        assert metrics.solidity == pytest.approx(1.0, rel=1e-6)

    def test_beam_metrics_are_exact(self, mesh_of):
        mesh = mesh_of("beam")
        metrics = compute_metrics(mesh, validate_and_repair(mesh))
        assert metrics.volume_mm3 == pytest.approx(120 * 12 * 8, rel=1e-9)
        assert metrics.aspect_ratio == pytest.approx(120 / 8)

    def test_repair_removes_degenerate_triangles(self):
        mesh = trimesh.creation.box(extents=(10, 10, 10))
        faces = np.vstack([mesh.faces, [[0, 0, 1]]])
        broken = trimesh.Trimesh(vertices=mesh.vertices, faces=faces, process=False)
        validation = validate_and_repair(broken)
        assert validation.degenerate_face_count >= 1
        assert any("degenerate" in entry for entry in validation.repairs_applied)

    def test_non_watertight_mesh_is_flagged_not_crashed(self, mesh_of):
        mesh = mesh_of("cube")
        open_mesh = trimesh.Trimesh(
            vertices=mesh.vertices, faces=mesh.faces[:-4], process=False
        )
        validation = validate_and_repair(open_mesh)
        metrics = compute_metrics(open_mesh, validation)
        assert metrics.volume_mm3 >= 0.0
        if not validation.is_watertight:
            assert validation.warnings

    def test_decimation_respects_budget(self):
        sphere = trimesh.creation.icosphere(subdivisions=4)
        simplified, changed = decimate_for_analysis(sphere, 500)
        assert changed
        assert len(simplified.faces) <= 700  # decimation is approximate


class TestTransforms:
    def test_build_direction_round_trip(self):
        for angles in [(0, 0, 0), (37, 8, 12), (90, 0, 0), (-45, 30, 170)]:
            rotation = rotation_from_euler_xyz(*angles)
            direction = build_direction_in_part_frame(rotation)
            assert np.linalg.norm(direction) == pytest.approx(1.0)
            # Rotating the part must send that direction onto world +Z.
            assert rotation @ direction == pytest.approx([0, 0, 1], abs=1e-9)

    def test_euler_round_trip(self):
        rotation = rotation_from_euler_xyz(37, 8, 12)
        again = rotation_from_euler_xyz(*euler_xyz_from_rotation(rotation))
        assert again == pytest.approx(rotation, abs=1e-9)

    def test_alignment_rotation(self):
        for direction in [[0, 0, 1], [0, 0, -1], [1, 0, 0], [0.3, -0.5, 0.8]]:
            d = np.array(direction, dtype=float)
            d /= np.linalg.norm(d)
            rotation = rotation_aligning_vector_to_z(d)
            assert rotation @ d == pytest.approx([0, 0, 1], abs=1e-9)
            assert np.linalg.det(rotation) == pytest.approx(1.0)

    def test_minimum_footprint_rectangle_of_rotated_rectangle(self):
        rectangle = np.array([[0, 0], [40, 0], [40, 10], [0, 10]], dtype=float)
        angle = np.radians(31.0)
        rotation = np.array(
            [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
        )
        rotated = rectangle @ rotation.T
        _, area, size = minimum_footprint_z_rotation(rotated)
        assert area == pytest.approx(400.0, rel=1e-6)
        assert sorted(size) == pytest.approx([10.0, 40.0], rel=1e-6)


class TestSections:
    def test_rectangular_section_properties_match_theory(self, mesh_of):
        """For the 120x12x8 beam cut perpendicular to X the section is a
        12x8 rectangle: A = 96, I about the 12 mm axis = b h^3 / 12."""
        mesh = mesh_of("beam")
        section = section_at(mesh, np.array([0.0, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]))
        assert section is not None
        assert section.area == pytest.approx(96.0, rel=1e-6)
        assert section.polar_moment == pytest.approx(
            12 * 8**3 / 12 + 8 * 12**3 / 12, rel=1e-6
        )
        principal = np.linalg.eigvalsh(section.inertia)
        assert sorted(principal) == pytest.approx(
            sorted([12 * 8**3 / 12, 8 * 12**3 / 12]), rel=1e-6
        )

    def test_cylinder_section_matches_theory(self, mesh_of):
        mesh = mesh_of("cylinder")  # radius 12, 64 sections
        section = section_at(mesh, np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
        assert section is not None
        # A regular 64-gon is slightly smaller than the circle it inscribes.
        assert section.area == pytest.approx(np.pi * 12**2, rel=0.01)
        assert section.polar_moment == pytest.approx(np.pi * 12**4 / 2, rel=0.02)

    def test_shell_fraction_is_higher_for_a_thin_walled_section(self, mesh_of):
        mesh = mesh_of("beam")
        origin, normal = np.zeros(3), np.array([1.0, 0.0, 0.0])
        thin = shell_inertia_fraction(mesh, origin, normal, 0.8)
        thick = shell_inertia_fraction(mesh, origin, normal, 2.0)
        assert thin is not None and thick is not None
        assert 0.0 < thin["shell_inertia_fraction"] < thick["shell_inertia_fraction"] <= 1.0
        # Walls always contribute more stiffness than area - that is the whole
        # point of the wall-versus-infill argument.
        assert thin["shell_inertia_fraction"] > thin["shell_area_fraction"]


class TestRegionPicking:
    def test_picks_a_whole_flat_face(self, mesh_of):
        mesh = mesh_of("cube")
        result = pick_region(mesh, 0, angle_tolerance_deg=5.0)
        assert len(result["face_ids"]) == 2  # a cube face is two triangles
        assert result["area_mm2"] == pytest.approx(400.0)
        assert result["is_planar"]

    def test_tolerance_zero_does_not_cross_an_edge(self, mesh_of):
        mesh = mesh_of("cube")
        result = pick_region(mesh, 0, angle_tolerance_deg=0.0)
        assert len(result["face_ids"]) <= 2

    def test_rejects_out_of_range_face(self, mesh_of):
        with pytest.raises(IndexError):
            pick_region(mesh_of("cube"), 10_000)
