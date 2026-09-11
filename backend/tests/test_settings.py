"""Print settings engine tests."""

from __future__ import annotations

import pytest

from app.geometry.prepared import prepare
from app.materials import database as materials_db
from app.mechanics.solver import ApproximateMechanicalSolver
from app.orientation.optimizer import OrientationOptimizer
from app.printing.printers import all_printers, get_printer
from app.printing.settings_optimizer import PrintSettingsOptimizer, SettingsInput
from app.schemas import (
    Constraint,
    CustomMaterial,
    LoadCase,
    LoadType,
    MaterialSelection,
    OptimizationWeights,
    Preset,
    ProcessConfig,
    SearchConfig,
)

FAST_SEARCH = SearchConfig(coarse_step_deg=30.0, refine_candidates=2, enable_fine_stage=False)


def candidate_for(mesh, material_id="petg", loads=None, constraints=None, nozzle=0.4):
    material = materials_db.get_material(material_id)
    model = ApproximateMechanicalSolver().analyze(mesh, material, loads or [], constraints or [])
    optimizer = OrientationOptimizer(
        prepared=prepare(mesh),
        material=material,
        mechanical_model=model,
        process=ProcessConfig(nozzle_diameter_mm=nozzle),
        printer=get_printer("bambu_x1c"),
        weights=OptimizationWeights(),
        search_config=FAST_SEARCH,
    )
    outcome = optimizer.optimize()
    return outcome.candidates[0], material, model


def recommend(mesh, preset=Preset.balanced, material_id="petg", nozzle=0.4, loads=None,
              constraints=None, min_wall=None, printer_id="bambu_x1c"):
    candidate, material, model = candidate_for(mesh, material_id, loads, constraints, nozzle)
    return PrintSettingsOptimizer().recommend(
        SettingsInput(
            mesh=mesh,
            candidate=candidate,
            material=material,
            printer=get_printer(printer_id),
            process=ProcessConfig(nozzle_diameter_mm=nozzle),
            preset=preset,
            mechanical_model=model,
            mechanical=candidate.mechanical,
            min_wall_thickness_mm=min_wall,
            has_torsion=any(
                load.type is LoadType.torsion for load in (loads or [])
            ),
            load_case_count=len(loads or []),
        )
    )


class TestLayerHeight:
    @pytest.mark.parametrize("nozzle", [0.2, 0.4, 0.6, 0.8])
    def test_layer_height_stays_inside_the_nozzle_limits(self, mesh_of, nozzle):
        result = recommend(mesh_of("bracket"), nozzle=nozzle)
        assert 0.25 * nozzle - 1e-9 <= result.layer_height <= 0.75 * nozzle + 1e-9
        assert result.nozzle == nozzle

    def test_strength_uses_finer_layers_than_speed(self, mesh_of):
        mesh = mesh_of("bracket")
        assert (
            recommend(mesh, Preset.max_strength).layer_height
            < recommend(mesh, Preset.fast).layer_height
        )

    def test_explicit_layer_height_is_respected(self, mesh_of):
        mesh = mesh_of("bracket")
        candidate, material, model = candidate_for(mesh)
        result = PrintSettingsOptimizer().recommend(
            SettingsInput(
                mesh=mesh,
                candidate=candidate,
                material=material,
                printer=get_printer("bambu_x1c"),
                process=ProcessConfig(layer_height_mm=0.12),
                preset=Preset.balanced,
                mechanical_model=model,
                mechanical=candidate.mechanical,
            )
        )
        assert result.layer_height == pytest.approx(0.12)
        assert any("explicitly" in reason for reason in result.reasons)


class TestWallsVersusInfill:
    def test_wall_decision_is_backed_by_a_measured_shell_fraction(self, mesh_of):
        result = recommend(mesh_of("beam"), Preset.max_strength)
        analysis = result.wall_vs_infill
        assert analysis["available"]
        selected = analysis["selected"]
        assert selected["loops"] == result.wall_loops
        assert 0.0 < selected["shell_inertia_fraction"] <= 1.0
        # The walls must always carry more stiffness than their share of area -
        # that is the argument for walls over infill.
        assert selected["shell_inertia_fraction"] > selected["shell_area_fraction"]

    def test_strength_priority_uses_at_least_as_many_walls_as_speed(self, mesh_of):
        mesh = mesh_of("beam")
        assert (
            recommend(mesh, Preset.max_strength).wall_loops
            >= recommend(mesh, Preset.fast).wall_loops
        )

    def test_strength_priority_never_uses_less_infill_than_lightweight(self, mesh_of):
        mesh = mesh_of("bracket")
        assert (
            recommend(mesh, Preset.max_strength).infill_density
            >= recommend(mesh, Preset.lightweight).infill_density
        )

    def test_explanation_mentions_the_measured_fraction(self, mesh_of):
        result = recommend(mesh_of("beam"), Preset.max_strength)
        assert any("bending stiffness" in reason for reason in result.reasons)

    def test_lightweight_uses_lightning_infill(self, mesh_of):
        assert recommend(mesh_of("bracket"), Preset.lightweight).infill_pattern == "lightning"

    def test_torsion_selects_an_isotropic_pattern(self, mesh_of):
        mesh = mesh_of("cylinder")
        centers = mesh.triangles_center
        fixed = [i for i, c in enumerate(centers) if c[2] < -29]
        loads = [LoadCase(id="t", name="Torsion", type=LoadType.torsion, torque_nm=[0, 0, 10])]
        result = recommend(
            mesh, Preset.balanced, loads=loads, constraints=[Constraint(face_ids=fixed)]
        )
        assert result.infill_pattern == "gyroid"


class TestSupportsAndBrim:
    def test_supports_follow_the_overhang_analysis(self, mesh_of):
        result = recommend(mesh_of("cube"))
        assert result.supports is False
        assert result.support_type == "none"

    def test_support_angle_matches_the_analysis_threshold(self, mesh_of):
        mesh = mesh_of("bracket")
        candidate, material, model = candidate_for(mesh)
        process = ProcessConfig(overhang_threshold_deg=55.0)
        result = PrintSettingsOptimizer().recommend(
            SettingsInput(
                mesh=mesh, candidate=candidate, material=material,
                printer=get_printer("bambu_x1c"), process=process, preset=Preset.balanced,
                mechanical_model=model, mechanical=candidate.mechanical,
            )
        )
        assert result.support_angle == pytest.approx(55.0)

    def test_high_warping_material_gets_a_brim(self, mesh_of):
        result = recommend(mesh_of("beam"), material_id="pc", printer_id="generic_open_i3")
        assert result.brim
        assert result.brim_width_mm > 0


class TestWarnings:
    def test_enclosure_requirement_is_warned_on_an_open_printer(self, mesh_of):
        result = recommend(mesh_of("cube"), material_id="abs", printer_id="generic_open_i3")
        assert any("enclosed chamber" in warning for warning in result.warnings)

    def test_fibre_filled_material_warns_about_the_nozzle(self, mesh_of):
        result = recommend(mesh_of("cube"), material_id="pa-cf")
        assert any("hardened nozzle" in warning for warning in result.warnings)

    def test_unprintable_wall_is_warned(self, mesh_of):
        result = recommend(mesh_of("cube"), nozzle=0.4, min_wall=0.3)
        assert any("below the 0.4 mm nozzle" in warning for warning in result.warnings)

    def test_single_line_wall_is_warned(self, mesh_of):
        result = recommend(mesh_of("cube"), nozzle=0.4, min_wall=0.6)
        assert any("single extrusion line" in warning for warning in result.warnings)


class TestMaterialsAndPrinters:
    def test_custom_material_is_accepted(self):
        custom = CustomMaterial(
            name="My blend",
            density_g_cm3=1.3,
            young_modulus_mpa=2500,
            tensile_strength_mpa=55,
            layer_adhesion_factor=0.6,
        )
        material = materials_db.resolve(MaterialSelection(custom=custom))
        assert material.is_custom
        assert materials_db.in_plane_tensile_strength(material) == 55
        assert materials_db.interlayer_tensile_strength(material) == pytest.approx(33.0)

    def test_custom_material_inherits_from_a_base(self):
        custom = CustomMaterial(
            name="Tuned PETG",
            density_g_cm3=1.27,
            young_modulus_mpa=2000,
            tensile_strength_mpa=50,
            layer_adhesion_factor=0.8,
            base_material_id="petg",
        )
        material = materials_db.resolve(MaterialSelection(custom=custom))
        assert material.print_defaults.recommended_layer_height.value is not None
        assert material.properties.warp_tendency.value is not None

    def test_selection_requires_exactly_one_source(self):
        with pytest.raises(ValueError):
            MaterialSelection()
        with pytest.raises(ValueError):
            MaterialSelection(
                material_id="petg",
                custom=CustomMaterial(
                    name="x", density_g_cm3=1, young_modulus_mpa=1,
                    tensile_strength_mpa=1, layer_adhesion_factor=0.5,
                ),
            )

    def test_every_property_carries_a_source_and_confidence(self):
        for material in materials_db.all_materials():
            for name in ("density", "young_modulus", "tensile_strength",
                         "layer_adhesion_factor", "warp_tendency"):
                prop = getattr(material.properties, name)
                assert prop.value is not None, f"{material.id}.{name}"
                assert prop.source, f"{material.id}.{name} has no source"
                assert prop.confidence.value in ("high", "medium", "low", "unknown")
                if prop.min is not None and prop.max is not None:
                    assert prop.min <= prop.value <= prop.max, f"{material.id}.{name}"

    def test_heuristic_properties_are_labelled(self):
        for material in materials_db.all_materials():
            for name in ("layer_adhesion_factor", "warp_tendency"):
                source = getattr(material.properties, name).source or ""
                assert "HEURISTIC" in source, f"{material.id}.{name}"

    def test_printers_declare_their_nozzles_and_volume(self):
        for printer in all_printers():
            assert printer.default_nozzle_mm in printer.available_nozzles_mm
            assert len(printer.build_volume_mm) == 3
            assert all(value > 0 for value in printer.build_volume_mm)
            assert printer.source

    def test_x1c_profile_matches_the_published_specification(self):
        printer = get_printer("bambu_x1c")
        assert printer.build_volume_mm == [256.0, 256.0, 256.0]
        assert printer.enclosed
        assert printer.available_nozzles_mm == [0.2, 0.4, 0.6, 0.8]


class TestMassEstimate:
    def test_mass_is_volume_times_density(self):
        material = materials_db.get_material("pla")
        volume = 10_000.0  # mm^3 = 10 cm^3
        expected = 10.0 * material.properties.density.value
        assert materials_db.mass_estimate_g(material, volume) == pytest.approx(expected)

    def test_mass_scales_with_infill(self):
        material = materials_db.get_material("pla")
        full = materials_db.mass_estimate_g(material, 1000.0, 1.0)
        half = materials_db.mass_estimate_g(material, 1000.0, 0.5)
        assert half == pytest.approx(full / 2)
