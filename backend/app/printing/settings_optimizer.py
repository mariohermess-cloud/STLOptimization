"""Print parameter recommendation.

``PrintSettingsOptimizer`` turns the chosen orientation, the material, the
nozzle and the mechanical result into concrete slicer settings, and states why
each value was chosen.

The wall-versus-infill decision is the part that is usually done by folklore
("more infill is stronger"). Here it is computed: the critical cross-section
is offset inwards by the wall thickness, and the fraction of the section's
second moment of area that the walls provide is measured. When the walls carry
the bending stiffness, walls are added and infill is left low; when the section
is compact and the load is carried through the bulk, infill is raised instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import trimesh

from app.geometry.sections import shell_inertia_fraction
from app.mechanics.solver import MechanicalEvaluation, MechanicalModel
from app.orientation.optimizer import RawCandidate
from app.schemas import Material, Preset, PrintSettings, Printer, ProcessConfig

#: Extrusion line width as a multiple of the nozzle diameter (slicer default).
LINE_WIDTH_FACTOR = 1.05
#: Layer height limits as a fraction of the nozzle diameter. Below the lower
#: bound the extrusion cannot be controlled; above the upper bound the layer
#: does not bond reliably to the one below.
LAYER_HEIGHT_LIMITS = (0.25, 0.75)
#: Solid skin thickness targets in mm, by priority.
SKIN_THICKNESS_MM = {
    Preset.max_strength: 1.2,
    Preset.balanced: 0.9,
    Preset.fast: 0.7,
    Preset.lightweight: 0.7,
    Preset.custom: 0.9,
}
#: Share of the section's bending stiffness the walls should provide, by
#: priority. The wall count is raised until this is met or the walls would
#: consume the section.
SHELL_STIFFNESS_TARGET = {
    Preset.max_strength: 0.75,
    Preset.balanced: 0.55,
    Preset.fast: 0.35,
    Preset.lightweight: 0.40,
    Preset.custom: 0.55,
}
LAYER_HEIGHT_PRIORITY_FACTOR = {
    Preset.max_strength: 0.80,
    Preset.balanced: 1.00,
    Preset.fast: 1.30,
    Preset.lightweight: 1.10,
    Preset.custom: 1.00,
}
INFILL_LIMITS = {
    Preset.max_strength: (0.25, 0.80),
    Preset.balanced: (0.15, 0.50),
    Preset.fast: (0.10, 0.30),
    Preset.lightweight: (0.05, 0.25),
    Preset.custom: (0.10, 0.60),
}
MAX_WALL_LOOPS = 8


@dataclass
class SettingsInput:
    mesh: trimesh.Trimesh
    candidate: RawCandidate
    material: Material
    printer: Printer
    process: ProcessConfig
    preset: Preset
    mechanical_model: MechanicalModel | None
    mechanical: MechanicalEvaluation | None
    min_wall_thickness_mm: float | None = None
    has_torsion: bool = False
    load_case_count: int = 0


class PrintSettingsOptimizer:
    def recommend(self, data: SettingsInput) -> PrintSettings:
        reasons: list[str] = []
        warnings: list[str] = []

        nozzle = data.process.nozzle_diameter_mm
        line_width = nozzle * LINE_WIDTH_FACTOR

        layer_height = self._layer_height(data, nozzle, reasons, warnings)
        first_layer = self._first_layer_height(layer_height, nozzle)

        wall_loops, wall_analysis = self._wall_loops(data, line_width, reasons, warnings)
        infill_density = self._infill(data, wall_analysis, reasons)
        infill_pattern = self._infill_pattern(data, reasons)

        skin = SKIN_THICKNESS_MM.get(data.preset, 0.9)
        top_layers = max(3, math.ceil(skin / layer_height))
        bottom_layers = max(3, math.ceil(skin / layer_height))
        reasons.append(
            f"{top_layers} top and {bottom_layers} bottom layers give a solid skin of about "
            f"{top_layers * layer_height:.2f} mm at {layer_height:.2f} mm layers."
        )

        supports, support_type = self._supports(data, reasons, warnings)
        brim, brim_width = self._brim(data, reasons)

        self._material_warnings(data, layer_height, nozzle, warnings)

        candidate = data.candidate
        return PrintSettings(
            printer=data.printer.name,
            material=data.material.name,
            nozzle=nozzle,
            orientation={
                "x": round(candidate.euler[0], 2),
                "y": round(candidate.euler[1], 2),
                "z": round(candidate.euler[2], 2),
            },
            layer_height=round(layer_height, 3),
            first_layer_height=round(first_layer, 3),
            wall_loops=wall_loops,
            top_layers=top_layers,
            bottom_layers=bottom_layers,
            infill_density=round(infill_density, 3),
            infill_pattern=infill_pattern,
            supports=supports,
            support_angle=round(data.process.overhang_threshold_deg, 1),
            support_type=support_type,
            brim=brim,
            brim_width_mm=round(brim_width, 1),
            reasons=reasons,
            warnings=warnings,
            wall_vs_infill=wall_analysis,
        )

    # -- individual decisions ---------------------------------------------
    def _layer_height(
        self, data: SettingsInput, nozzle: float, reasons: list[str], warnings: list[str]
    ) -> float:
        if data.process.layer_height_mm:
            reasons.append(
                f"Layer height {data.process.layer_height_mm:.2f} mm was set explicitly by the user."
            )
            return float(data.process.layer_height_mm)

        recommended = data.material.print_defaults.recommended_layer_height
        base = (recommended.value or 0.5 * 0.4) * (nozzle / 0.4)
        base *= LAYER_HEIGHT_PRIORITY_FACTOR.get(data.preset, 1.0)

        low = LAYER_HEIGHT_LIMITS[0] * nozzle
        high = LAYER_HEIGHT_LIMITS[1] * nozzle
        if recommended.min:
            low = max(low, float(recommended.min) * (nozzle / 0.4))
        if recommended.max:
            high = min(high, float(recommended.max) * (nozzle / 0.4))

        value = min(max(base, low), high)

        if data.min_wall_thickness_mm and data.min_wall_thickness_mm < 2 * nozzle:
            capped = min(value, 0.5 * nozzle)
            if capped < value:
                value = capped
                reasons.append(
                    f"Layer height reduced to {value:.2f} mm because the part has walls down to "
                    f"{data.min_wall_thickness_mm:.2f} mm, which need finer layers to resolve."
                )

        value = round(value / 0.02) * 0.02
        value = min(max(value, low), high)

        if data.preset is Preset.max_strength:
            reasons.append(
                f"Layer height {value:.2f} mm: thinner layers for the strength priority - more, "
                "better-consolidated interfaces and finer detail, at the cost of print time."
            )
        elif data.preset is Preset.fast:
            reasons.append(
                f"Layer height {value:.2f} mm: the largest height this nozzle supports reliably "
                f"(limit {high:.2f} mm) to minimise the layer count."
            )
        else:
            reasons.append(
                f"Layer height {value:.2f} mm, from the material recommendation scaled to the "
                f"{nozzle:.1f} mm nozzle."
            )
        return float(value)

    @staticmethod
    def _first_layer_height(layer_height: float, nozzle: float) -> float:
        return float(min(max(layer_height, 0.5 * nozzle), 0.75 * nozzle))

    def _wall_loops(
        self, data: SettingsInput, line_width: float, reasons: list[str], warnings: list[str]
    ) -> tuple[int, dict]:
        """Choose the wall count from the section's shell stiffness fraction."""
        origin, normal = self._critical_section(data)
        target = SHELL_STIFFNESS_TARGET.get(data.preset, 0.55)
        utilization = data.mechanical.max_utilization if data.mechanical else 0.0
        if utilization > 0.5:
            target = min(0.9, target + 0.10)

        base = int(data.material.print_defaults.recommended_wall_count.value or 3)
        analysis: dict = {
            "method": "shell second-moment fraction at the critical section",
            "target_shell_inertia_fraction": target,
            "line_width_mm": round(line_width, 3),
        }

        if origin is None or normal is None:
            reasons.append(
                f"{base} walls from the material recommendation; no cross-section was available "
                "to analyse the wall contribution."
            )
            analysis["available"] = False
            return base, analysis

        analysis["available"] = True
        chosen = base
        measured = None
        for loops in range(2, MAX_WALL_LOOPS + 1):
            result = shell_inertia_fraction(data.mesh, origin, normal, loops * line_width)
            if result is None:
                break
            measured = result
            analysis[f"loops_{loops}"] = {
                "shell_inertia_fraction": round(result["shell_inertia_fraction"], 3),
                "shell_area_fraction": round(result["shell_area_fraction"], 3),
            }
            if result["core_is_empty"]:
                chosen = loops
                analysis["section_fully_solid_at_loops"] = loops
                break
            if result["shell_inertia_fraction"] >= target:
                chosen = loops
                break
            chosen = loops

        chosen = max(2, min(MAX_WALL_LOOPS, max(chosen, 2)))
        if measured is not None:
            final = shell_inertia_fraction(data.mesh, origin, normal, chosen * line_width)
            if final is not None:
                analysis["selected"] = {
                    "loops": chosen,
                    "wall_thickness_mm": round(chosen * line_width, 2),
                    "shell_inertia_fraction": round(final["shell_inertia_fraction"], 3),
                    "shell_area_fraction": round(final["shell_area_fraction"], 3),
                    "section_area_mm2": round(final["section_area_mm2"], 1),
                    "core_is_empty": bool(final["core_is_empty"]),
                }
                fraction = final["shell_inertia_fraction"]
                if fraction >= 0.6:
                    reasons.append(
                        f"{chosen} walls ({chosen * line_width:.2f} mm) carry "
                        f"{fraction * 100:.0f} % of the bending stiffness of the critical "
                        f"section ({final['section_area_mm2']:.0f} mm^2). Strength here is "
                        "perimeter dominated, so walls are the effective lever - not infill."
                    )
                else:
                    reasons.append(
                        f"{chosen} walls ({chosen * line_width:.2f} mm) provide "
                        f"{fraction * 100:.0f} % of the section's bending stiffness. The section "
                        "is compact, so the core carries a large share and infill matters more "
                        "than extra walls here."
                    )
                if final["core_is_empty"]:
                    reasons.append(
                        "At this wall count the critical section is solid; infill density has "
                        "almost no effect on its strength."
                    )
        return chosen, analysis

    def _infill(self, data: SettingsInput, wall_analysis: dict, reasons: list[str]) -> float:
        low, high = INFILL_LIMITS.get(data.preset, (0.10, 0.60))
        base = float(data.material.print_defaults.recommended_infill.value or 0.20)

        selected = wall_analysis.get("selected") or {}
        shell_fraction = float(selected.get("shell_inertia_fraction", 0.5))
        core_share = max(0.0, 1.0 - shell_fraction)
        utilization = data.mechanical.max_utilization if data.mechanical else 0.0

        # The core only matters to the extent that it carries the section, and
        # only matters much when the section is actually working hard.
        value = base + 0.45 * core_share * min(1.0, utilization * 2.0)
        if data.preset is Preset.max_strength:
            value += 0.10
        value = min(max(value, low), high)

        if selected.get("core_is_empty"):
            value = low
            reasons.append(
                f"Infill set to the minimum of {value * 100:.0f} % - the walls already fill the "
                "critical section."
            )
        elif utilization > 0.05:
            reasons.append(
                f"Infill {value * 100:.0f} %: the core carries {core_share * 100:.0f} % of the "
                f"section's bending stiffness at a utilisation of {utilization:.2f}."
            )
        else:
            reasons.append(
                f"Infill {value * 100:.0f} % from the material recommendation; the defined load "
                "barely stresses the part."
            )
        return value

    def _infill_pattern(self, data: SettingsInput, reasons: list[str]) -> str:
        if data.preset is Preset.lightweight:
            reasons.append(
                "Lightning infill: it supports the top surfaces with the least material. It "
                "contributes almost nothing to strength."
            )
            return "lightning"
        if data.preset is Preset.fast:
            reasons.append("Grid infill: fastest to print of the patterns considered.")
            return "grid"
        if data.has_torsion or data.load_case_count > 1:
            reasons.append(
                "Gyroid infill: the load is multi-directional"
                + (" and includes torsion" if data.has_torsion else "")
                + ", and gyroid is close to isotropic in all three axes."
            )
            return "gyroid"
        if data.preset is Preset.max_strength:
            reasons.append(
                "Gyroid infill: near-isotropic behaviour and continuous extrusion paths, which "
                "suits a strength-driven part."
            )
            return "gyroid"
        reasons.append("Grid infill: a balanced default for print time and stiffness.")
        return "grid"

    def _supports(
        self, data: SettingsInput, reasons: list[str], warnings: list[str]
    ) -> tuple[bool, str]:
        overhangs = data.candidate.overhangs
        if not data.process.allow_supports:
            if overhangs.overhang_area_mm2 > 0:
                warnings.append(
                    f"Supports are disabled, but {overhangs.overhang_area_mm2:.0f} mm^2 of surface "
                    f"is below the {overhangs.threshold_deg:.0f} deg threshold and will sag."
                )
            return False, "none"

        if overhangs.overhang_area_mm2 <= 1e-6:
            reasons.append("No supports needed in this orientation.")
            return False, "none"

        # Near-horizontal ceilings need a dense, flat support interface; steep
        # local overhangs are better served by tree supports.
        if overhangs.steepest_overhang_deg < 20.0:
            support_type = "normal(auto)"
            reasons.append(
                f"Normal supports: the steepest overhang is {overhangs.steepest_overhang_deg:.0f} deg "
                "from the plate, i.e. nearly horizontal ceilings that need a flat interface."
            )
        else:
            support_type = "tree(auto)"
            reasons.append(
                f"Tree supports: overhangs are localised (steepest {overhangs.steepest_overhang_deg:.0f} deg), "
                "so tree supports use less material and mark the surface less."
            )
        reasons.append(
            f"Support threshold {data.process.overhang_threshold_deg:.0f} deg, matching the angle "
            f"used in the orientation analysis ({overhangs.support_volume_mm3:.0f} mm^3 estimated)."
        )
        return True, support_type

    def _brim(self, data: SettingsInput, reasons: list[str]) -> tuple[bool, float]:
        bed = data.candidate.bed
        warp = data.candidate.warping
        if warp.level == "HIGH":
            reasons.append(f"Brim: warping risk is HIGH (index {warp.risk_index:.2f}).")
            return True, 8.0
        if warp.level == "MEDIUM":
            reasons.append(f"Brim: warping risk is MEDIUM (index {warp.risk_index:.2f}).")
            return True, 5.0
        if bed.contact_area_mm2 < 200.0:
            reasons.append(
                f"Brim: the bed contact area is only {bed.contact_area_mm2:.0f} mm^2."
            )
            return True, 5.0
        if not bed.com_inside:
            reasons.append("Brim: the centre of mass lies outside the bed contact area.")
            return True, 8.0
        reasons.append("No brim: low warping risk and sufficient bed contact.")
        return False, 0.0

    def _material_warnings(
        self, data: SettingsInput, layer_height: float, nozzle: float, warnings: list[str]
    ) -> None:
        if data.material.requires_enclosure and not data.printer.enclosed:
            warnings.append(
                f"{data.material.name} normally needs an enclosed chamber; "
                f"{data.printer.name} is not enclosed. Expect reduced layer adhesion and warping."
            )
        if data.material.fiber_filled:
            warnings.append(
                f"{data.material.name} is abrasive - a hardened nozzle is required."
            )
        if data.min_wall_thickness_mm and data.min_wall_thickness_mm < nozzle:
            warnings.append(
                f"The part has walls down to {data.min_wall_thickness_mm:.2f} mm, which is below "
                f"the {nozzle:.1f} mm nozzle diameter. Those features cannot be printed; use a "
                "smaller nozzle or thicken the geometry."
            )
        elif data.min_wall_thickness_mm and data.min_wall_thickness_mm < 2 * nozzle:
            warnings.append(
                f"Walls down to {data.min_wall_thickness_mm:.2f} mm will be printed as a single "
                f"extrusion line with a {nozzle:.1f} mm nozzle and will be weak."
            )
        for note in data.material.notes:
            warnings.append(note)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _critical_section(data: SettingsInput) -> tuple[np.ndarray | None, np.ndarray | None]:
        """Where to measure the wall contribution.

        With a load case: the section that the mechanical analysis found most
        highly utilised. Without one: a cut through the centre of mass
        perpendicular to the longest dimension of the part, which is the
        section a bending load would most likely find.
        """
        if data.mechanical is not None and data.mechanical.critical_point is not None:
            point = data.mechanical.critical_point
            return point.section_position, point.section_normal
        try:
            extents = np.asarray(data.mesh.extents, dtype=float)
            axis = np.zeros(3)
            axis[int(np.argmax(extents))] = 1.0
            centre = np.asarray(data.mesh.center_mass, dtype=float)
            if not np.isfinite(centre).all():
                centre = np.asarray(data.mesh.centroid, dtype=float)
            return centre, axis
        except Exception:  # noqa: BLE001
            return None, None
