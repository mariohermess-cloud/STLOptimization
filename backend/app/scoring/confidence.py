"""Analysis confidence.

The confidence figure is not a quality badge for the software. It measures how
much of the input the result actually rests on: mesh quality, material data
provenance, how completely the load case is specified, the method used for the
mechanical analysis, and how clearly the winning orientation beats the runner
up. Each factor is reported with its own score and a sentence explaining it,
so a low number can be acted on.
"""

from __future__ import annotations

from app.geometry.analyzer import MeshValidation
from app.schemas import ConfidenceFactor, ConfidenceReport, Confidence, Material

CONFIDENCE_SCORES = {
    Confidence.high: 95.0,
    Confidence.medium: 70.0,
    Confidence.low: 40.0,
    Confidence.unknown: 10.0,
}

#: The approximate solver is a section analysis, not FEM. This ceiling is
#: fixed and deliberate: no amount of clean input turns beam formulas into a
#: validated stress analysis.
APPROXIMATE_SOLVER_CONFIDENCE = 45.0
FEM_SOLVER_CONFIDENCE = 80.0


def build_confidence(
    *,
    validation: MeshValidation,
    material: Material,
    decimated: bool,
    triangle_count: int,
    has_loads: bool,
    has_constraints: bool,
    load_regions_explicit: bool,
    solver_is_approximation: bool,
    top_score: float | None,
    runner_up_score: float | None,
) -> ConfidenceReport:
    factors: list[ConfidenceFactor] = []
    limitations: list[str] = []

    # --- mesh quality ---------------------------------------------------
    mesh_score = 100.0
    mesh_notes: list[str] = []
    if not validation.is_watertight:
        mesh_score -= 45.0
        mesh_notes.append("not watertight")
        limitations.append(
            "The mesh is not watertight; volume, mass and cross-section results are approximate."
        )
    if not validation.is_winding_consistent:
        mesh_score -= 25.0
        mesh_notes.append("inconsistent winding")
    if validation.degenerate_face_count:
        mesh_score -= 5.0
        mesh_notes.append(f"{validation.degenerate_face_count} degenerate triangles removed")
    if validation.body_count > 1:
        mesh_score -= 10.0
        mesh_notes.append(f"{validation.body_count} separate bodies")
    factors.append(
        ConfidenceFactor(
            key="mesh_quality",
            label="Mesh quality",
            score=max(0.0, mesh_score),
            weight=0.20,
            detail="Watertight and consistently wound mesh."
            if not mesh_notes
            else "Issues found: " + ", ".join(mesh_notes) + ".",
        )
    )

    # --- geometric resolution -------------------------------------------
    resolution_score = 100.0
    resolution_detail = f"{triangle_count:,} triangles used for the orientation search."
    if decimated:
        resolution_score = 75.0
        resolution_detail = (
            f"The mesh was simplified to {triangle_count:,} triangles for the orientation "
            "search; areas and normals carry a small extra error."
        )
        limitations.append(
            "Orientation scores were computed on a simplified mesh (see the model summary)."
        )
    elif triangle_count < 200:
        resolution_score = 60.0
        resolution_detail = (
            f"Only {triangle_count} triangles - a very coarse tessellation limits the "
            "overhang and contact analysis."
        )
    factors.append(
        ConfidenceFactor(
            key="geometry_resolution",
            label="Geometric resolution",
            score=resolution_score,
            weight=0.10,
            detail=resolution_detail,
        )
    )

    # --- material data ---------------------------------------------------
    used = {
        "density": material.properties.density,
        "tensile strength": material.properties.tensile_strength,
        "Young's modulus": material.properties.young_modulus,
        "layer adhesion factor": material.properties.layer_adhesion_factor,
    }
    scores = []
    weakest: list[str] = []
    for name, prop in used.items():
        if prop.value is None:
            scores.append(CONFIDENCE_SCORES[Confidence.unknown])
            weakest.append(f"{name} unknown")
            continue
        scores.append(CONFIDENCE_SCORES[prop.confidence])
        if prop.confidence in (Confidence.low, Confidence.unknown):
            weakest.append(f"{name} is low confidence")
    material_score = sum(scores) / len(scores)
    factors.append(
        ConfidenceFactor(
            key="material_data",
            label="Material data",
            score=material_score,
            weight=0.20,
            detail=(
                f"Properties of {material.name} from the material database."
                if not weakest
                else f"{material.name}: " + ", ".join(weakest) + "."
            ),
        )
    )
    if weakest:
        limitations.append(
            "Material properties are literature-typical values, not measurements of the "
            "filament in use. Verify against the technical data sheet."
        )

    # --- load definition --------------------------------------------------
    if not has_loads:
        load_score = 0.0
        load_detail = "No load case was defined - the ranking is printability only."
        limitations.append("No mechanical load was defined; no strength statement is made.")
    else:
        load_score = 100.0
        issues: list[str] = []
        if not has_constraints:
            load_score -= 40.0
            issues.append("no fixed surface selected (the centre of mass was used as reaction)")
        if not load_regions_explicit:
            load_score -= 25.0
            issues.append(
                "the load application region was not selected, so the most distant point was used"
            )
        load_detail = (
            "Loads, fixture and application regions are fully specified."
            if not issues
            else "Incomplete: " + "; ".join(issues) + "."
        )
        if issues:
            limitations.append(
                "The boundary conditions were partly inferred. Select the loaded and fixed "
                "faces for a meaningful stress result."
            )
    factors.append(
        ConfidenceFactor(
            key="load_definition",
            label="Load and fixture definition",
            score=max(0.0, load_score),
            weight=0.20,
            detail=load_detail,
        )
    )

    # --- solver -----------------------------------------------------------
    solver_score = APPROXIMATE_SOLVER_CONFIDENCE if solver_is_approximation else FEM_SOLVER_CONFIDENCE
    factors.append(
        ConfidenceFactor(
            key="mechanical_method",
            label="Mechanical method",
            score=solver_score,
            weight=0.15,
            detail=(
                "Beam theory on real cross-sections (APPROXIMATION). Stress concentrations, "
                "contact and buckling are not resolved."
                if solver_is_approximation
                else "Finite element analysis."
            ),
        )
    )
    if solver_is_approximation and has_loads:
        limitations.append(
            "The mechanical analysis is a section-based approximation, not a finite element "
            "analysis. Stress concentrations at holes, fillets and sharp transitions are not "
            "resolved."
        )

    # --- ranking margin ----------------------------------------------------
    if top_score is None or runner_up_score is None:
        margin_score = 50.0
        margin_detail = "Only one orientation was evaluated."
    else:
        margin = top_score - runner_up_score
        margin_score = float(min(100.0, 40.0 + margin * 20.0))
        if margin < 1.0:
            margin_detail = (
                f"The top two orientations are within {margin:.2f} points of each other; "
                "the choice between them is not strongly supported by the data."
            )
        else:
            margin_detail = (
                f"The best orientation leads the runner-up by {margin:.1f} points."
            )
    factors.append(
        ConfidenceFactor(
            key="ranking_margin",
            label="Ranking margin",
            score=margin_score,
            weight=0.15,
            detail=margin_detail,
        )
    )

    total_weight = sum(factor.weight for factor in factors)
    score = sum(factor.score * factor.weight for factor in factors) / total_weight

    limitations.append(
        "This tool provides engineering-oriented estimates and FDM print optimisation "
        "guidance. It is not a certified structural analysis system."
    )

    return ConfidenceReport(score=round(score, 1), factors=factors, limitations=limitations)
