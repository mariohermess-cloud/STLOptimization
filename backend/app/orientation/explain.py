"""Human readable justification for an orientation.

Every sentence produced here is generated from a number that is also shown in
the score breakdown, and the thresholds are fixed. There is no separate
narrative model that could disagree with the analysis: if the text says the
support requirement is low, it is because ``support_score`` is high.
"""

from __future__ import annotations

import numpy as np

from app.orientation.optimizer import RawCandidate
from app.schemas import Material, ProcessConfig


def _format_minutes(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60.0:.1f} h"


def explain(
    candidate: RawCandidate,
    *,
    material: Material,
    process: ProcessConfig,
    fastest_minutes: float,
    has_loads: bool,
    printer_name: str,
    build_volume: list[float],
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []

    # -- mechanical ------------------------------------------------------
    if has_loads and candidate.mechanical is not None:
        mech = candidate.mechanical
        detail = mech.critical_detail or {}
        angle = detail.get("angle_to_layer_plane_deg")
        score = mech.mechanical_score
        case = detail.get("load_case_name", "the governing load case")

        if angle is not None:
            if score >= 95:
                reasons.append(
                    f"The principal stress of {case} lies {angle:.0f} deg from the layer plane, "
                    f"so the weak interlayer direction is barely loaded "
                    f"({score:.0f} % of the isotropic capability is retained)."
                )
            elif score >= 75:
                reasons.append(
                    f"The principal stress of {case} lies {angle:.0f} deg from the layer plane; "
                    f"the orientation retains {score:.0f} % of the isotropic capability."
                )
            else:
                warnings.append(
                    f"The principal stress of {case} is {angle:.0f} deg from the layer plane, so "
                    f"layer adhesion carries a large share of it - only {score:.0f} % of the "
                    "isotropic capability is retained in this orientation."
                )
        if detail.get("interlayer_governs"):
            warnings.append(
                "Failure is predicted at a layer interface rather than in the bulk material."
            )
        if np.isfinite(mech.min_safety_factor):
            if mech.min_safety_factor < 1.0:
                warnings.append(
                    f"Estimated safety factor {mech.min_safety_factor:.2f} against the material "
                    "tensile strength - the part is predicted to fail under the defined load."
                )
            elif mech.min_safety_factor < 1.5:
                warnings.append(
                    f"Estimated safety factor is only {mech.min_safety_factor:.2f}. Consider more "
                    "walls, a stronger material, or reducing the load."
                )
            else:
                reasons.append(
                    f"Estimated safety factor {mech.min_safety_factor:.1f} at the critical section "
                    f"(A = {detail.get('section_area_mm2', 0.0):.0f} mm^2)."
                )
        if candidate.scores["layer"] >= 85:
            reasons.append(
                f"{candidate.scores['layer']:.0f} % of the dominant stress acts inside the layer plane."
            )

    # -- support and overhang -------------------------------------------
    support = candidate.overhangs
    part_volume = max(candidate.print_time.extruded_volume_mm3, 1e-6)
    support_ratio = support.support_volume_mm3 / part_volume
    if support.overhang_area_mm2 <= 1e-6:
        reasons.append(
            f"No face falls below the {support.threshold_deg:.0f} deg overhang threshold - "
            "the part prints without support."
        )
    elif support_ratio < 0.05:
        reasons.append(
            f"Low support requirement: {support.overhang_area_mm2:.0f} mm^2 of overhang, "
            f"about {support.support_volume_mm3:.0f} mm^3 of support material."
        )
    else:
        warnings.append(
            f"{support.overhang_area_fraction * 100:.0f} % of the surface needs support "
            f"({support.support_volume_mm3:.0f} mm^3 estimated support material, "
            f"steepest overhang {support.steepest_overhang_deg:.0f} deg from the plate)."
        )

    # -- bed contact and stability ---------------------------------------
    bed = candidate.bed
    if bed.contact_area_mm2 >= 100.0:
        reasons.append(
            f"Bed contact area {bed.contact_area_mm2:.0f} mm^2 over a "
            f"{candidate.footprint_mm[0]:.0f} x {candidate.footprint_mm[1]:.0f} mm footprint."
        )
    else:
        warnings.append(
            f"Small bed contact area ({bed.contact_area_mm2:.0f} mm^2). A brim or raft is advisable."
        )
    if not bed.com_inside:
        warnings.append(
            "The centre of mass projects outside the bed contact area - the part is unstable "
            "on the plate in this orientation."
        )
    elif bed.com_height_mm > 0 and bed.com_margin_mm > 0:
        tipping = np.degrees(np.arctan2(bed.com_margin_mm, bed.com_height_mm))
        if tipping >= 30:
            reasons.append(
                f"Stable stance: the centre of mass sits {bed.com_margin_mm:.0f} mm inside the "
                f"footprint at {bed.com_height_mm:.0f} mm height ({tipping:.0f} deg tipping margin)."
            )
        elif tipping < 15:
            warnings.append(
                f"Tall and narrow in this orientation ({tipping:.0f} deg tipping margin, "
                f"slenderness {bed.slenderness:.1f})."
            )

    # -- warping ----------------------------------------------------------
    warp = candidate.warping
    if warp.level == "LOW":
        reasons.append(f"Warping risk assessed as LOW (index {warp.risk_index:.2f}).")
    else:
        drivers = "; ".join(warp.drivers) if warp.drivers else "long bonded span on the plate"
        warnings.append(
            f"Warping risk {warp.level} (index {warp.risk_index:.2f}): {drivers}."
        )

    # -- time and material -------------------------------------------------
    minutes = candidate.print_time.minutes
    if fastest_minutes > 0 and minutes > fastest_minutes * 1.05:
        warnings.append(
            f"Estimated print time {_format_minutes(minutes)}, "
            f"{(minutes / fastest_minutes - 1) * 100:.0f} % above the fastest orientation found "
            f"({candidate.print_time.layer_count} layers). APPROXIMATION."
        )
    else:
        reasons.append(
            f"Estimated print time {_format_minutes(minutes)} "
            f"({candidate.print_time.layer_count} layers). APPROXIMATION."
        )

    if not candidate.fits_build_volume:
        warnings.append(
            f"Does not fit the {printer_name} build volume of "
            f"{build_volume[0]:.0f} x {build_volume[1]:.0f} x {build_volume[2]:.0f} mm in this orientation."
        )

    if material.properties.layer_adhesion_factor.confidence.value in ("low", "unknown") and has_loads:
        warnings.append(
            "The layer adhesion factor for this material is a low-confidence heuristic; "
            "the mechanical ranking is more reliable than the absolute safety factor."
        )

    return reasons, warnings
