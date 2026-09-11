"""Orientation engine regression tests.

The cases here are constructed so the correct answer follows from the physics
rather than from the implementation:

* a beam in bending has a stress direction along its own axis, so any build
  direction perpendicular to that axis keeps the stress in the layer plane and
  any build direction along it does not;
* a block with a large horizontal ceiling needs support in one orientation and
  none in another;
* a tall thin part standing on its small end is less stable than lying down.

The assertions are on relationships and orderings, never on specific score
values.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.geometry.prepared import prepare
from app.materials import database as materials_db
from app.mechanics.solver import ApproximateMechanicalSolver
from app.orientation import search
from app.orientation.optimizer import OrientationOptimizer
from app.orientation.presets import PRESET_WEIGHTS, weights_for
from app.printing.printers import get_printer
from app.schemas import (
    Constraint,
    LoadCase,
    LoadType,
    OptimizationWeights,
    Preset,
    ProcessConfig,
    SearchConfig,
)
from app.scoring import printability

FAST_SEARCH = SearchConfig(
    coarse_step_deg=20.0, refine_candidates=3, fine_candidates=1, enable_fine_stage=False
)


def build_optimizer(mesh, loads, constraints, material_id="petg", weights=None, search_config=None):
    material = materials_db.get_material(material_id)
    model = ApproximateMechanicalSolver().analyze(mesh, material, loads, constraints)
    return OrientationOptimizer(
        prepared=prepare(mesh),
        material=material,
        mechanical_model=model,
        process=ProcessConfig(),
        printer=get_printer("bambu_x1c"),
        weights=weights or OptimizationWeights(),
        search_config=search_config or FAST_SEARCH,
    )


def beam_setup(mesh, force=(0.0, 0.0, -200.0)):
    centers = mesh.triangles_center
    fixed = [i for i, c in enumerate(centers) if c[0] < -55]
    loaded = [i for i, c in enumerate(centers) if c[0] > 55]
    loads = [
        LoadCase(
            id="tip",
            name="Tip load",
            type=LoadType.bending,
            force_n=list(force),
            application_face_ids=loaded,
        )
    ]
    return loads, [Constraint(face_ids=fixed)]


class TestSearchGeneration:
    def test_grid_is_dense_enough_and_normalised(self):
        directions = search.sphere_grid(15.0)
        assert len(directions) > 150
        assert np.allclose(np.linalg.norm(directions, axis=1), 1.0)

    def test_every_direction_has_a_grid_neighbour_within_the_step(self):
        """A 15 degree grid must not leave holes bigger than 15 degrees."""
        grid = search.sphere_grid(15.0)
        rng = np.random.default_rng(7)
        probes = rng.normal(size=(200, 3))
        probes /= np.linalg.norm(probes, axis=1)[:, None]
        worst = np.degrees(np.arccos(np.clip((grid @ probes.T).max(axis=0), -1, 1))).max()
        assert worst <= 15.0

    def test_flat_face_directions_include_the_cube_faces(self, mesh_of):
        directions = search.flat_face_directions(mesh_of("cube"))
        assert len(directions) == 6
        for axis in np.eye(3):
            assert any(abs(abs(float(d @ axis)) - 1.0) < 1e-6 for d in directions)

    def test_refinement_stays_inside_the_cone(self):
        seed = np.array([0.0, 0.0, 1.0])
        refined = search.refine_around(seed, 15.0, 5.0)
        angles = np.degrees(np.arccos(np.clip(refined @ seed, -1, 1)))
        assert angles.max() <= 15.0 + 1e-6
        assert len(refined) > 5

    def test_deduplicate_collapses_near_duplicates(self):
        directions = np.array([[0, 0, 1], [0, 0.01, 0.9999], [1, 0, 0]], dtype=float)
        assert len(search.deduplicate(directions, tolerance_deg=2.0)) == 2


class TestBendingBeamRegression:
    """A beam loaded in bending must prefer an orientation where the bending
    stress lies in the layer plane."""

    def test_stress_in_layer_plane_scores_better_than_across_layers(self, mesh_of):
        mesh = mesh_of("beam")
        loads, constraints = beam_setup(mesh)
        optimizer = build_optimizer(mesh, loads, constraints)

        # Build direction along the beam axis puts the bending stress across
        # the layers; perpendicular to the axis keeps it in the layer plane.
        across = optimizer.evaluate(np.array([1.0, 0.0, 0.0]))
        in_plane = optimizer.evaluate(np.array([0.0, 0.0, 1.0]))

        assert in_plane.scores["mechanical"] > across.scores["mechanical"]
        assert in_plane.scores["layer"] > across.scores["layer"]
        assert in_plane.mechanical.min_safety_factor > across.mechanical.min_safety_factor

    def test_mechanical_score_floor_is_the_layer_adhesion_factor(self, mesh_of):
        """Across layers, an orientation can lose exactly the material's
        adhesion factor and no more."""
        mesh = mesh_of("beam")
        loads, constraints = beam_setup(mesh)
        optimizer = build_optimizer(mesh, loads, constraints)
        material = materials_db.get_material("petg")
        factor = material.properties.layer_adhesion_factor.value

        across = optimizer.evaluate(np.array([1.0, 0.0, 0.0]))
        assert across.scores["mechanical"] == pytest.approx(100.0 * factor, rel=0.02)

    def test_score_is_monotonic_in_the_angle_to_the_layer_plane(self, mesh_of):
        mesh = mesh_of("beam")
        loads, constraints = beam_setup(mesh)
        optimizer = build_optimizer(mesh, loads, constraints)

        scores = []
        for angle in range(0, 91, 15):
            radians = np.radians(angle)
            direction = np.array([np.sin(radians), 0.0, np.cos(radians)])
            scores.append(optimizer.evaluate(direction).scores["layer"])
        assert all(b <= a + 1e-6 for a, b in zip(scores, scores[1:])), scores

    def test_weaker_layer_adhesion_penalises_the_bad_orientation_more(self, mesh_of):
        mesh = mesh_of("beam")
        loads, constraints = beam_setup(mesh)
        # PLA-CF (factor 0.40) versus PETG (factor 0.70).
        weak = build_optimizer(mesh, loads, constraints, material_id="pla-cf")
        strong = build_optimizer(mesh, loads, constraints, material_id="petg")
        direction = np.array([1.0, 0.0, 0.0])
        assert (
            weak.evaluate(direction).scores["mechanical"]
            < strong.evaluate(direction).scores["mechanical"]
        )

    def test_full_search_avoids_standing_the_beam_on_its_end(self, mesh_of):
        mesh = mesh_of("beam")
        loads, constraints = beam_setup(mesh)
        optimizer = build_optimizer(
            mesh, loads, constraints, weights=weights_for(Preset.max_strength, OptimizationWeights())
        )
        outcome = optimizer.optimize()
        best = outcome.candidates[0]
        # The beam axis is X; the winning build direction must not be along it.
        assert abs(float(best.direction @ np.array([1.0, 0.0, 0.0]))) < 0.5
        assert best.scores["mechanical"] > 90.0

    def test_load_along_the_beam_axis_changes_the_preferred_orientation(self, mesh_of):
        """Sanity check that the engine follows the load, not the shape: a
        force along the beam axis makes the axial direction the stressed one,
        which is a different geometry-independent preference than bending."""
        mesh = mesh_of("beam")
        loads, constraints = beam_setup(mesh, force=(500.0, 0.0, 0.0))
        optimizer = build_optimizer(mesh, loads, constraints)
        across = optimizer.evaluate(np.array([1.0, 0.0, 0.0]))
        in_plane = optimizer.evaluate(np.array([0.0, 0.0, 1.0]))
        assert across.scores["mechanical"] < in_plane.scores["mechanical"]


class TestPrintabilityScores:
    def test_overhang_detection_on_a_known_ceiling(self, mesh_of):
        """The overhang fixture is a C-shape: a 32 x 30 mm horizontal ceiling
        spans the gap between the base and the upper arm. Built along Z that
        ceiling is unsupported; built along X the same surface is a vertical
        wall and needs nothing."""
        mesh = mesh_of("overhang_test")
        prepared = prepare(mesh)

        up = printability.analyse_overhangs(
            prepared, np.array([0.0, 0.0, 1.0]), threshold_deg=45.0, bed_tolerance_mm=0.2
        )
        sideways = printability.analyse_overhangs(
            prepared, np.array([1.0, 0.0, 0.0]), threshold_deg=45.0, bed_tolerance_mm=0.2
        )
        assert up.overhang_area_mm2 > 500.0
        assert up.steepest_overhang_deg == pytest.approx(0.0, abs=1e-6)
        assert sideways.overhang_area_mm2 == pytest.approx(0.0)
        assert up.score < sideways.score
        assert up.support_score < sideways.support_score

    def test_a_cube_never_needs_support(self, mesh_of):
        prepared = prepare(mesh_of("cube"))
        for direction in np.eye(3):
            result = printability.analyse_overhangs(
                prepared, direction, threshold_deg=45.0, bed_tolerance_mm=0.2
            )
            assert result.overhang_area_mm2 == pytest.approx(0.0)
            assert result.score == 100.0

    def test_a_higher_threshold_never_finds_less_overhang(self, mesh_of):
        prepared = prepare(mesh_of("bracket"))
        direction = np.array([0.3, 0.2, 0.93])
        direction /= np.linalg.norm(direction)
        areas = [
            printability.analyse_overhangs(
                prepared, direction, threshold_deg=threshold, bed_tolerance_mm=0.2
            ).overhang_area_mm2
            for threshold in (45.0, 50.0, 55.0, 60.0)
        ]
        assert all(b >= a - 1e-9 for a, b in zip(areas, areas[1:])), areas

    def test_bed_contact_and_height_for_a_lying_versus_standing_beam(self, mesh_of):
        mesh = mesh_of("beam")
        prepared = prepare(mesh)

        def bed(direction):
            d = np.asarray(direction, dtype=float)
            overhangs = printability.analyse_overhangs(
                prepared, d, threshold_deg=45.0, bed_tolerance_mm=0.2
            )
            return printability.analyse_bed(
                prepared, d, bed_tolerance_mm=0.2, overhangs=overhangs
            )

        lying = bed([0.0, 0.0, 1.0])  # 120 x 12 on the plate, 8 mm tall
        standing = bed([1.0, 0.0, 0.0])  # 12 x 8 on the plate, 120 mm tall

        assert lying.height_mm == pytest.approx(8.0)
        assert standing.height_mm == pytest.approx(120.0)
        assert lying.contact_area_mm2 == pytest.approx(120 * 12, rel=1e-6)
        assert standing.contact_area_mm2 == pytest.approx(12 * 8, rel=1e-6)
        assert lying.score > standing.score
        assert standing.slenderness > lying.slenderness

    def test_stability_penalises_a_centre_of_mass_outside_the_footprint(self, mesh_of):
        mesh = mesh_of("bracket")
        prepared = prepare(mesh)
        scores = []
        for direction in search.sphere_grid(30.0):
            overhangs = printability.analyse_overhangs(
                prepared, direction, threshold_deg=45.0, bed_tolerance_mm=0.2
            )
            bed = printability.analyse_bed(
                prepared, direction, bed_tolerance_mm=0.2, overhangs=overhangs
            )
            scores.append((bed.com_inside, bed.score))
        unstable = [score for inside, score in scores if not inside]
        stable = [score for inside, score in scores if inside]
        if unstable and stable:
            assert max(unstable) <= max(stable)

    def test_warping_risk_is_lower_in_an_enclosure(self, mesh_of):
        mesh = mesh_of("beam")
        prepared = prepare(mesh)
        direction = np.array([0.0, 0.0, 1.0])
        overhangs = printability.analyse_overhangs(
            prepared, direction, threshold_deg=45.0, bed_tolerance_mm=0.2
        )
        bed = printability.analyse_bed(prepared, direction, bed_tolerance_mm=0.2, overhangs=overhangs)

        enclosed = printability.analyse_warping(
            bed, warp_tendency=0.85, enclosed=True, material_name="ABS"
        )
        open_frame = printability.analyse_warping(
            bed, warp_tendency=0.85, enclosed=False, material_name="ABS"
        )
        low_shrinkage = printability.analyse_warping(
            bed, warp_tendency=0.15, enclosed=False, material_name="PLA"
        )
        assert enclosed.risk_index < open_frame.risk_index
        assert low_shrinkage.risk_index < open_frame.risk_index
        assert enclosed.score > open_frame.score
        assert open_frame.level in ("LOW", "MEDIUM", "HIGH")

    def test_print_time_grows_with_the_layer_count(self, mesh_of):
        mesh = mesh_of("beam")
        prepared = prepare(mesh)

        def estimate(direction):
            d = np.asarray(direction, dtype=float)
            overhangs = printability.analyse_overhangs(
                prepared, d, threshold_deg=45.0, bed_tolerance_mm=0.2
            )
            bed = printability.analyse_bed(prepared, d, bed_tolerance_mm=0.2, overhangs=overhangs)
            return printability.estimate_print_time(
                prepared, bed, overhangs, layer_height_mm=0.2, max_volumetric_flow=14.0
            )

        lying = estimate([0, 0, 1])
        standing = estimate([1, 0, 0])
        assert standing.layer_count > lying.layer_count
        assert standing.minutes > lying.minutes


class TestScoringAndWeights:
    def test_overall_score_is_the_weighted_sum_of_its_components(self, mesh_of):
        mesh = mesh_of("bracket")
        optimizer = build_optimizer(mesh, [], [])
        outcome = optimizer.optimize()
        for candidate in outcome.candidates:
            total = sum(component["contribution"] for component in candidate.components)
            assert candidate.overall == pytest.approx(total, abs=0.05)
            for component in candidate.components:
                assert component["contribution"] == pytest.approx(
                    component["weight"] * component["value"], abs=0.05
                )

    def test_weights_are_normalised(self):
        weights = OptimizationWeights(mechanical=7, layer=3, support=0, overhang=0,
                                      stability=0, warping=0, material=0, print_time=0)
        normalized = weights.normalized()
        assert sum(normalized.values()) == pytest.approx(1.0)
        assert normalized["mechanical"] == pytest.approx(0.7)

    def test_zero_weights_are_rejected(self):
        weights = OptimizationWeights(mechanical=0, layer=0, support=0, overhang=0,
                                      stability=0, warping=0, material=0, print_time=0)
        with pytest.raises(ValueError):
            weights.normalized()

    def test_every_preset_is_normalisable_and_distinct(self):
        seen = []
        for preset, weights in PRESET_WEIGHTS.items():
            normalized = weights.normalized()
            assert sum(normalized.values()) == pytest.approx(1.0)
            seen.append(tuple(round(v, 4) for v in normalized.values()))
        assert len(set(seen)) == len(seen)

    def test_strength_preset_weights_mechanics_above_time(self):
        weights = PRESET_WEIGHTS[Preset.max_strength].normalized()
        fast = PRESET_WEIGHTS[Preset.fast].normalized()
        assert weights["mechanical"] > weights["print_time"]
        assert fast["print_time"] > fast["mechanical"]

    def test_weights_change_the_ranking(self, mesh_of):
        mesh = mesh_of("overhang_test")
        loads, constraints = [], []
        support_first = build_optimizer(
            mesh, loads, constraints,
            weights=OptimizationWeights(mechanical=0, layer=0, support=1, overhang=0,
                                        stability=0, warping=0, material=0, print_time=0),
        ).optimize()
        time_first = build_optimizer(
            mesh, loads, constraints,
            weights=OptimizationWeights(mechanical=0, layer=0, support=0, overhang=0,
                                        stability=0, warping=0, material=0, print_time=1),
        ).optimize()
        assert support_first.candidates[0].scores["support"] >= time_first.candidates[0].scores["support"]
        assert time_first.candidates[0].print_time.minutes <= support_first.candidates[0].print_time.minutes

    def test_printability_only_redistributes_the_mechanical_weight(self, mesh_of):
        optimizer = build_optimizer(mesh_of("cube"), [], [])
        outcome = optimizer.optimize()
        assert outcome.weights["mechanical"] == 0.0
        assert outcome.weights["layer"] == 0.0
        assert sum(outcome.weights.values()) == pytest.approx(1.0)
        assert any("no mechanical load" in w.lower() for w in outcome.warnings)


class TestSearchBehaviour:
    def test_results_are_distinct_orientations(self, mesh_of):
        outcome = build_optimizer(mesh_of("bracket"), [], []).optimize()
        directions = [candidate.direction for candidate in outcome.candidates]
        for i, a in enumerate(directions):
            for b in directions[i + 1 :]:
                assert float(a @ b) < np.cos(np.radians(7.0))

    def test_refinement_does_not_make_the_result_worse(self, mesh_of):
        mesh = mesh_of("bracket")
        coarse = build_optimizer(
            mesh, [], [],
            search_config=SearchConfig(coarse_step_deg=20.0, refine_candidates=1,
                                       enable_fine_stage=False),
        ).optimize()
        refined = build_optimizer(
            mesh, [], [],
            search_config=SearchConfig(coarse_step_deg=20.0, refine_step_deg=5.0,
                                       refine_candidates=6, fine_candidates=3,
                                       enable_fine_stage=True),
        ).optimize()
        assert refined.candidates[0].overall >= coarse.candidates[0].overall - 1e-6
        assert refined.evaluated > coarse.evaluated

    def test_results_are_deterministic(self, mesh_of):
        mesh = mesh_of("bracket")
        first = build_optimizer(mesh, [], []).optimize()
        second = build_optimizer(mesh, [], []).optimize()
        assert [c.overall for c in first.candidates] == pytest.approx(
            [c.overall for c in second.candidates]
        )

    def test_oversized_part_is_reported_as_not_fitting(self, mesh_of):
        import trimesh

        oversized = trimesh.creation.box(extents=(400.0, 50.0, 50.0))
        outcome = build_optimizer(oversized, [], []).optimize()
        assert all(not candidate.fits_build_volume for candidate in outcome.candidates)
        assert any("build volume" in warning for warning in outcome.warnings)

    def test_requested_orientation_is_evaluated(self, mesh_of):
        mesh = mesh_of("cube")
        outcome = build_optimizer(mesh, [], []).optimize(extra_orientations=[[37.0, 8.0, 12.0]])
        assert outcome.candidates
