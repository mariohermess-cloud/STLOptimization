"""Mechanical solver and anisotropy tests.

These check *relationships* that follow from the documented physics, and a few
closed-form beam results where the fixture makes them exact. They deliberately
do not assert specific score values, which would only lock in whatever the
code happened to produce.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.materials import database as materials_db
from app.mechanics import anisotropy
from app.mechanics.solver import ApproximateMechanicalSolver
from app.schemas import Constraint, LoadCase, LoadType


@pytest.fixture
def petg():
    return materials_db.get_material("petg")


def beam_faces(mesh):
    centers = mesh.triangles_center
    fixed = [i for i, c in enumerate(centers) if c[0] < -55]
    loaded = [i for i, c in enumerate(centers) if c[0] > 55]
    return fixed, loaded


class TestAnisotropyModel:
    def test_hankinson_endpoints(self):
        assert anisotropy.hankinson_allowable(50.0, 25.0, 0.0) == pytest.approx(50.0)
        assert anisotropy.hankinson_allowable(50.0, 25.0, 1.0) == pytest.approx(25.0)

    def test_hankinson_is_monotonic(self):
        values = [anisotropy.hankinson_allowable(50.0, 25.0, c) for c in np.linspace(0, 1, 11)]
        assert all(b <= a + 1e-9 for a, b in zip(values, values[1:]))

    def test_uniaxial_stress_across_layers_is_the_worst_case(self):
        stress = np.diag([10.0, 0.0, 0.0])
        strengths = dict(sigma_par=50.0, sigma_perp=25.0, tau_par=29.0, tau_perp=14.5)

        across = anisotropy.utilization(stress, [1.0, 0.0, 0.0], **strengths)
        in_plane = anisotropy.utilization(stress, [0.0, 0.0, 1.0], **strengths)

        assert across["utilization"] > in_plane["utilization"]
        assert across["interlayer_governs"]
        assert not in_plane["interlayer_governs"]
        # In-plane loading loses nothing to anisotropy.
        assert in_plane["anisotropy_penalty"] == pytest.approx(1.0)
        # Across layers the penalty is exactly the strength ratio.
        assert across["anisotropy_penalty"] == pytest.approx(25.0 / 50.0, rel=1e-6)

    def test_compression_does_not_open_the_interface(self):
        strengths = dict(sigma_par=50.0, sigma_perp=25.0, tau_par=29.0, tau_perp=14.5)
        tension = anisotropy.utilization(np.diag([10.0, 0, 0]), [1, 0, 0], **strengths)
        compression = anisotropy.utilization(np.diag([-10.0, 0, 0]), [1, 0, 0], **strengths)
        assert compression["utilization"] < tension["utilization"]
        assert compression["utilization"] == pytest.approx(10.0 / 50.0, rel=1e-9)

    def test_penalty_never_exceeds_one(self):
        rng = np.random.default_rng(0)
        strengths = dict(sigma_par=50.0, sigma_perp=25.0, tau_par=29.0, tau_perp=14.5)
        for _ in range(50):
            tensor = rng.normal(size=(3, 3)) * 5
            tensor = tensor + tensor.T
            direction = rng.normal(size=3)
            result = anisotropy.utilization(tensor, direction, **strengths)
            assert 0.0 < result["anisotropy_penalty"] <= 1.0 + 1e-9

    def test_in_plane_fraction_endpoints(self):
        stress = np.diag([10.0, 0.0, 0.0])
        assert anisotropy.in_plane_fraction(stress, [1, 0, 0]) == pytest.approx(0.0, abs=1e-9)
        assert anisotropy.in_plane_fraction(stress, [0, 0, 1]) == pytest.approx(1.0)


class TestMaterialDerivations:
    def test_interlayer_strength_uses_the_adhesion_factor(self, petg):
        factor = petg.properties.layer_adhesion_factor.value
        assert materials_db.interlayer_tensile_strength(petg) == pytest.approx(
            materials_db.in_plane_tensile_strength(petg) * factor
        )

    def test_shear_strengths_follow_von_mises(self, petg):
        assert materials_db.in_plane_shear_strength(petg) == pytest.approx(
            materials_db.in_plane_tensile_strength(petg) / np.sqrt(3.0)
        )

    def test_every_catalogue_material_has_the_properties_the_solver_needs(self):
        for material in materials_db.all_materials():
            assert materials_db.in_plane_tensile_strength(material) > 0
            assert 0 < materials_db.interlayer_tensile_strength(material) <= materials_db.in_plane_tensile_strength(material)
            assert materials_db.density_g_mm3(material) > 0
            assert material.properties.density.source
            assert material.properties.layer_adhesion_factor.source


class TestSectionSolver:
    def test_cantilever_bending_stress_matches_beam_theory(self, mesh_of, petg):
        """120 x 12 x 8 beam, clamped at x = -60, 100 N at x = +60 along -Z.

        The maximum bending stress at the clamped section is M/W with
        M = F * L and W = b h^2 / 6 for the 12 x 8 rectangle bent about the
        12 mm axis. The solver must reproduce that from the real section.
        """
        mesh = mesh_of("beam")
        fixed, loaded = beam_faces(mesh)
        load = LoadCase(
            id="tip",
            name="Tip load",
            type=LoadType.bending,
            force_n=[0.0, 0.0, -100.0],
            application_face_ids=loaded,
        )
        model = ApproximateMechanicalSolver().analyze(
            mesh, petg, [load], [Constraint(face_ids=fixed)]
        )
        assert model.points

        # The section closest to the clamp carries the full moment.
        first = min(model.points, key=lambda p: p.section_position[0])
        detail = first.detail
        lever = 60.0 - float(first.section_position[0])
        expected = (100.0 * lever) / (12 * 8**2 / 6)
        assert detail["sigma_bending_mpa"] == pytest.approx(expected, rel=0.02)
        assert first.section_area_mm2 == pytest.approx(96.0, rel=1e-3)

    def test_axial_stress_matches_force_over_area(self, mesh_of, petg):
        mesh = mesh_of("beam")
        fixed, loaded = beam_faces(mesh)
        load = LoadCase(
            id="pull",
            name="Pull",
            type=LoadType.tension,
            force_n=[960.0, 0.0, 0.0],
            application_face_ids=loaded,
        )
        model = ApproximateMechanicalSolver().analyze(
            mesh, petg, [load], [Constraint(face_ids=fixed)]
        )
        axial = [p.detail.get("sigma_axial_mpa") for p in model.points if "sigma_axial_mpa" in p.detail]
        assert axial
        # 960 N over 96 mm^2 is exactly 10 MPa, with no bending because the
        # force passes through the section centroid.
        assert max(axial) == pytest.approx(10.0, rel=1e-3)

    def test_torsion_produces_interlayer_shear_when_the_axis_is_the_build_axis(
        self, mesh_of, petg
    ):
        mesh = mesh_of("cylinder")  # axis along Z
        centers = mesh.triangles_center
        fixed = [i for i, c in enumerate(centers) if c[2] < -29]
        loaded = [i for i, c in enumerate(centers) if c[2] > 29]
        load = LoadCase(
            id="t",
            name="Torsion",
            type=LoadType.torsion,
            torque_nm=[0.0, 0.0, 20.0],
            application_face_ids=loaded,
        )
        model = ApproximateMechanicalSolver().analyze(
            mesh, petg, [load], [Constraint(face_ids=fixed)]
        )
        along_axis = model.evaluate(np.array([0.0, 0.0, 1.0]))
        across_axis = model.evaluate(np.array([1.0, 0.0, 0.0]))
        # Twisting about the build axis loads the layer interfaces in shear,
        # which is the weak plane; twisting about an in-plane axis does not.
        assert along_axis.max_utilization > across_axis.max_utilization
        assert along_axis.mechanical_score < across_axis.mechanical_score

    def test_declared_type_mismatch_is_warned_not_silently_reinterpreted(
        self, mesh_of, petg
    ):
        mesh = mesh_of("beam")
        fixed, loaded = beam_faces(mesh)
        load = LoadCase(
            id="mislabelled",
            name="Mislabelled",
            type=LoadType.tension,
            force_n=[0.0, 0.0, -100.0],
            application_face_ids=loaded,
        )
        model = ApproximateMechanicalSolver().analyze(
            mesh, petg, [load], [Constraint(face_ids=fixed)]
        )
        assert any("declared as tension" in warning for warning in model.warnings)

    def test_no_loads_gives_a_neutral_evaluation(self, mesh_of, petg):
        model = ApproximateMechanicalSolver().analyze(mesh_of("beam"), petg, [], [])
        assert not model.has_loads
        evaluation = model.evaluate(np.array([0.0, 0.0, 1.0]))
        assert evaluation.mechanical_score == 100.0
        assert evaluation.max_utilization == 0.0

    def test_multiple_load_cases_are_weighted(self, mesh_of, petg):
        mesh = mesh_of("beam")
        fixed, loaded = beam_faces(mesh)
        loads = [
            LoadCase(id="a", name="A", type=LoadType.bending, force_n=[0, 0, -100], weight=3.0,
                     application_face_ids=loaded),
            LoadCase(id="b", name="B", type=LoadType.bending, force_n=[0, -20, 0], weight=1.0,
                     application_face_ids=loaded),
        ]
        model = ApproximateMechanicalSolver().analyze(
            mesh, petg, loads, [Constraint(face_ids=fixed)]
        )
        assert model.load_weights["a"] == pytest.approx(0.75)
        assert model.load_weights["b"] == pytest.approx(0.25)
        evaluation = model.evaluate(np.array([0.0, 0.0, 1.0]))
        assert {c["load_case_id"] for c in evaluation.per_load_case} == {"a", "b"}

    def test_solver_is_labelled_as_an_approximation(self, mesh_of, petg):
        model = ApproximateMechanicalSolver().analyze(mesh_of("beam"), petg, [], [])
        assert model.is_approximation
        assert any("not a finite element" in a.lower() for a in model.assumptions)
