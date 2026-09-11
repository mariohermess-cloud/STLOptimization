"""Orchestration: from uploaded bytes to a ranked, explained result.

This module is the only place that knows the order of the pipeline. The API
layer handles HTTP and validation, the engines below handle physics; this sits
between them.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Sequence

import numpy as np

from app.core.config import settings
from app.core.errors import NoConstraintError, NoLoadCaseError
from app.core.logging_config import timed
from app.geometry import features
from app.geometry.analyzer import compute_metrics, decimate_for_analysis, validate_and_repair
from app.geometry.loader import load_stl, sanitize_filename
from app.geometry.prepared import prepare
from app.materials import database as materials_db
from app.mechanics.solver import ApproximateMechanicalSolver, MechanicalModel
from app.orientation.explain import explain
from app.orientation.optimizer import OrientationOptimizer, RawCandidate
from app.orientation.presets import weights_for
from app.printing.printers import get_printer
from app.printing.settings_optimizer import PrintSettingsOptimizer, SettingsInput
from app.scoring.confidence import build_confidence
from app.schemas import (
    BedMetrics,
    CriticalRegion,
    GeometryReport,
    LoadType,
    MechanicalMetrics,
    ModelSummary,
    OrientationCandidate,
    OrientationRequest,
    OrientationResult,
    PrintSettings,
    PrintTimeMetrics,
    ScoreComponent,
    SettingsRequest,
    SupportMetrics,
    WarpingMetrics,
)
from app.storage.store import ModelRecord, store

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "This tool provides engineering-oriented estimates and FDM print optimisation guidance. "
    "It is not a certified structural analysis system. Do not use it as the sole basis for a "
    "safety-critical decision."
)


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------
def ingest(data: bytes, filename: str | None) -> ModelRecord:
    with timed(logger, "upload", size_bytes=len(data)) as fields:
        mesh, fmt = load_stl(data)
        validation = validate_and_repair(mesh)
        metrics = compute_metrics(mesh, validation)
        analysis_mesh, decimated = decimate_for_analysis(mesh, settings.analysis_face_budget)
        prepared = prepare(analysis_mesh, volume_mm3=metrics.volume_mm3)

        record = store.add(
            data,
            filename=sanitize_filename(filename),
            stl_format=fmt.kind,
            mesh=mesh,
            analysis_mesh=analysis_mesh,
            prepared=prepared,
            validation=validation,
            metrics=metrics,
            decimated=decimated,
        )
        fields["model_id"] = record.id
        fields["triangles"] = metrics.triangle_count
        return record


def summarize(record: ModelRecord) -> ModelSummary:
    return ModelSummary(
        id=record.id,
        filename=record.filename,
        size_bytes=record.size_bytes,
        stl_format=record.stl_format,
        uploaded_at=record.uploaded_at,
        triangle_count=record.metrics.triangle_count,
        analysis_triangle_count=record.prepared.face_count,
        decimated=record.decimated,
        duplicate_of=record.duplicate_of,
    )


def geometry_report(record: ModelRecord, material_id: str | None = None) -> GeometryReport:
    mass = None
    material_name = None
    if material_id:
        material = materials_db.get_material(material_id)
        mass = materials_db.mass_estimate_g(material, record.metrics.volume_mm3)
        material_name = material.name

    warnings = list(record.validation.warnings)
    if record.decimated:
        warnings.append(
            f"The model has {record.metrics.triangle_count:,} triangles and was simplified to "
            f"{record.prepared.face_count:,} for the orientation search. Reported dimensions, "
            "volume and cross-sections use the full mesh."
        )

    return GeometryReport(
        validation={
            "is_watertight": record.validation.is_watertight,
            "is_winding_consistent": record.validation.is_winding_consistent,
            "is_volume": record.validation.is_volume,
            "euler_number": record.validation.euler_number,
            "body_count": record.validation.body_count,
            "boundary_edge_count": record.validation.boundary_edge_count,
            "degenerate_face_count": record.validation.degenerate_face_count,
            "duplicate_face_count": record.validation.duplicate_face_count,
            "repairs_applied": record.validation.repairs_applied,
            "volume_is_reliable": record.validation.volume_is_reliable,
        },
        metrics=record.metrics.to_dict(),
        mass_estimate_g=mass,
        mass_material=material_name,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# orientation optimisation
# --------------------------------------------------------------------------
def build_mechanical_model(record: ModelRecord, request: OrientationRequest) -> MechanicalModel:
    material = materials_db.resolve(request.material)
    solver = ApproximateMechanicalSolver()
    loads = [] if request.printability_only else list(request.loads)
    constraints = [] if request.printability_only else list(request.constraints)
    # The solver cuts real cross-sections, so it runs on the full resolution
    # mesh - the same mesh whose face indices the client selected on.
    return solver.analyze(record.mesh, material, loads, constraints)


def run_optimization(
    record: ModelRecord,
    request: OrientationRequest,
    progress: Callable[[float, str], None] | None = None,
) -> OrientationResult:
    started = time.time()

    if not request.printability_only:
        if not request.loads:
            raise NoLoadCaseError()
        if not any(constraint.face_ids for constraint in request.constraints):
            raise NoConstraintError()

    material = materials_db.resolve(request.material)
    printer = get_printer(request.process.printer_id)
    weights = weights_for(request.preset, request.weights)

    with timed(logger, "optimize_orientation", model_id=record.id) as fields:
        model = build_mechanical_model(record, request)

        optimizer = OrientationOptimizer(
            prepared=record.prepared,
            material=material,
            mechanical_model=model,
            process=request.process,
            printer=printer,
            weights=weights,
            search_config=request.search,
        )
        outcome = optimizer.optimize(
            extra_orientations=request.fixed_orientations, progress=progress
        )
        fields["evaluated"] = outcome.evaluated

    if progress:
        progress(0.95, "Detecting potentially critical geometric regions")

    fastest = min((c.print_time.minutes for c in outcome.candidates), default=0.0)
    candidates: list[OrientationCandidate] = []
    for rank, raw in enumerate(outcome.candidates, start=1):
        reasons, warnings = explain(
            raw,
            material=material,
            process=request.process,
            fastest_minutes=fastest,
            has_loads=model.has_loads,
            printer_name=printer.name,
            build_volume=printer.build_volume_mm,
        )
        candidates.append(_to_schema(raw, rank, reasons, warnings))

    regions = detect_critical_regions(record, request, outcome.candidates[0] if outcome.candidates else None)

    load_regions_explicit = bool(
        request.loads
        and all(load.application_face_ids or load.application_point for load in request.loads)
    )
    confidence = build_confidence(
        validation=record.validation,
        material=material,
        decimated=record.decimated,
        triangle_count=record.prepared.face_count,
        has_loads=model.has_loads,
        has_constraints=any(c.face_ids for c in request.constraints),
        load_regions_explicit=load_regions_explicit,
        solver_is_approximation=model.is_approximation,
        top_score=candidates[0].overall_score if candidates else None,
        runner_up_score=candidates[1].overall_score if len(candidates) > 1 else None,
    )

    result = OrientationResult(
        model_id=record.id,
        generated_at=started,
        candidates=candidates,
        confidence=confidence,
        critical_regions=regions,
        weights=outcome.weights,
        process=request.process,
        material=material,
        evaluated_candidates=outcome.evaluated,
        duration_seconds=outcome.duration_seconds,
        warnings=outcome.warnings + model.warnings,
        disclaimer=DISCLAIMER,
    )

    record.results["orientation"] = {
        "result": result,
        "raw": outcome.candidates,
        "model": model,
        "request": request,
    }
    return result


def detect_critical_regions(
    record: ModelRecord, request: OrientationRequest, best: RawCandidate | None
) -> list[CriticalRegion]:
    regions: list[CriticalRegion] = []
    with timed(logger, "critical_regions", model_id=record.id):
        samples = features.sample_thickness(record.analysis_mesh)
        thin, minimum = features.detect_thin_walls(
            record.analysis_mesh, request.process.nozzle_diameter_mm, samples
        )
        regions.extend(thin)
        record.metrics.min_wall_sample_mm = minimum

        try:
            regions.extend(features.detect_concave_features(record.analysis_mesh))
        except Exception:  # noqa: BLE001
            logger.info("concave feature detection failed", extra={"model_id": record.id})

        axis = np.asarray(record.metrics.pca_axes[0], dtype=float)
        try:
            regions.extend(features.detect_narrow_sections(record.analysis_mesh, axis))
        except Exception:  # noqa: BLE001
            logger.info("narrow section detection failed", extra={"model_id": record.id})

        if best is not None:
            regions.extend(
                features.detect_overhang_regions(
                    record.prepared,
                    best.overhangs.overhang_face_mask,
                    request.process.overhang_threshold_deg,
                )
            )
            if best.mechanical is not None and best.mechanical.critical_point is not None:
                point = best.mechanical.critical_point
                regions.append(
                    CriticalRegion(
                        id="bending-zone",
                        kind="bending_zone",
                        label=f"Highest utilisation: {best.mechanical.critical_load_case}",
                        severity="critical" if best.mechanical.max_utilization >= 1.0 else "warning",
                        description=(
                            f"The section analysis puts the highest utilisation "
                            f"({best.mechanical.max_utilization:.2f}, safety factor "
                            f"{best.mechanical.min_safety_factor:.2f}) at {point.label}, where the "
                            f"cross-section is {point.section_area_mm2:.0f} mm^2. This is a beam-theory "
                            "result, not a resolved stress field."
                        ),
                        position=[float(v) for v in point.position],
                        metric={
                            "utilization": best.mechanical.max_utilization,
                            "safety_factor": best.mechanical.min_safety_factor,
                            "section_area_mm2": point.section_area_mm2,
                            "section_modulus_mm3": point.section_modulus_mm3,
                        },
                    )
                )

    if record.decimated:
        # Face indices of the simplified analysis mesh do not address the mesh
        # the viewer loaded, so they are dropped rather than highlighting the
        # wrong triangles. The reported positions stay valid.
        for region in regions:
            region.face_ids = []
    return regions


def _to_schema(
    raw: RawCandidate, rank: int, reasons: list[str], warnings: list[str]
) -> OrientationCandidate:
    mechanical = None
    if raw.mechanical is not None:
        detail = raw.mechanical.critical_detail or {}
        point = raw.mechanical.critical_point
        mechanical = MechanicalMetrics(
            max_utilization=raw.mechanical.max_utilization,
            min_safety_factor=(
                raw.mechanical.min_safety_factor
                if np.isfinite(raw.mechanical.min_safety_factor)
                else 1e9
            ),
            critical_load_case=raw.mechanical.critical_load_case,
            critical_section_position_mm=(
                [float(v) for v in point.section_position] if point is not None else None
            ),
            critical_section_area_mm2=point.section_area_mm2 if point is not None else None,
            critical_section_modulus_mm3=point.section_modulus_mm3 if point is not None else None,
            max_stress_mpa=detail.get("von_mises_mpa"),
            allowable_stress_mpa=detail.get("allowable_stress_mpa"),
            stress_direction=detail.get("stress_direction"),
            angle_to_layer_plane_deg=detail.get("angle_to_layer_plane_deg"),
            per_load_case=raw.mechanical.per_load_case,
        )

    return OrientationCandidate(
        rank=rank,
        rotation_x=round(raw.euler[0], 2),
        rotation_y=round(raw.euler[1], 2),
        rotation_z=round(raw.euler[2], 2),
        build_direction_part_frame=[float(v) for v in raw.direction],
        mechanical_score=round(raw.scores["mechanical"], 1),
        layer_score=round(raw.scores["layer"], 1),
        support_score=round(raw.scores["support"], 1),
        overhang_score=round(raw.scores["overhang"], 1),
        stability_score=round(raw.scores["stability"], 1),
        warping_score=round(raw.scores["warping"], 1),
        print_time_score=round(raw.scores["print_time"], 1),
        material_score=round(raw.scores["material"], 1),
        overall_score=round(raw.overall, 1),
        components=[ScoreComponent(**component) for component in raw.components],
        support=SupportMetrics(
            overhang_area_mm2=round(raw.overhangs.overhang_area_mm2, 2),
            overhang_area_fraction=round(raw.overhangs.overhang_area_fraction, 4),
            estimated_support_volume_mm3=round(raw.overhangs.support_volume_mm3, 1),
            estimated_support_contact_area_mm2=round(raw.overhangs.support_contact_area_mm2, 1),
            steepest_overhang_deg=round(raw.overhangs.steepest_overhang_deg, 1),
            threshold_deg=raw.overhangs.threshold_deg,
        ),
        bed=BedMetrics(
            contact_area_mm2=round(raw.bed.contact_area_mm2, 2),
            footprint_area_mm2=round(raw.bed.footprint_area_mm2, 2),
            footprint_size_mm=[round(raw.footprint_mm[0], 2), round(raw.footprint_mm[1], 2)],
            height_mm=round(raw.bed.height_mm, 2),
            com_height_mm=round(raw.bed.com_height_mm, 2),
            com_offset_from_footprint_center_mm=round(raw.bed.com_margin_mm, 2),
            com_inside_footprint=raw.bed.com_inside,
            slenderness=round(raw.bed.slenderness, 3),
            fits_build_volume=raw.fits_build_volume,
        ),
        warping=WarpingMetrics(
            risk_index=round(raw.warping.risk_index, 3),
            level=raw.warping.level,
            max_bed_span_mm=round(raw.warping.max_bed_span_mm, 2),
            drivers=raw.warping.drivers,
        ),
        mechanical=mechanical,
        print_time=PrintTimeMetrics(
            estimated_minutes=round(raw.print_time.minutes, 1),
            layer_count=raw.print_time.layer_count,
            extruded_volume_mm3=round(raw.print_time.extruded_volume_mm3, 1),
            support_volume_mm3=round(raw.print_time.support_volume_mm3, 1),
        ),
        reasons=reasons,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# print settings
# --------------------------------------------------------------------------
def recommend_settings(record: ModelRecord, request: SettingsRequest) -> PrintSettings:
    stored = record.results.get("orientation")
    if not stored:
        from app.core.errors import AnalysisRequiredError

        raise AnalysisRequiredError(
            "Run the orientation optimisation before asking for print settings."
        )

    raw_candidates: Sequence[RawCandidate] = stored["raw"]
    index = min(max(request.candidate_rank, 1), len(raw_candidates)) - 1
    candidate = raw_candidates[index]
    model: MechanicalModel = stored["model"]

    material = materials_db.resolve(request.material)
    printer = get_printer(request.process.printer_id)

    loads = request.loads or stored["request"].loads
    has_torsion = any(
        load.type is LoadType.torsion or max(abs(v) for v in load.torque_nm) > 0 for load in loads
    )

    with timed(logger, "recommend_settings", model_id=record.id):
        settings_result = PrintSettingsOptimizer().recommend(
            SettingsInput(
                mesh=record.mesh,
                candidate=candidate,
                material=material,
                printer=printer,
                process=request.process,
                preset=request.preset,
                mechanical_model=model,
                mechanical=candidate.mechanical,
                min_wall_thickness_mm=record.metrics.min_wall_sample_mm,
                has_torsion=has_torsion,
                load_case_count=len(loads),
            )
        )
    record.results["settings"] = settings_result
    return settings_result


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def build_report(record: ModelRecord, candidate_rank: int = 1) -> dict[str, Any]:
    stored = record.results.get("orientation")
    if not stored:
        from app.core.errors import AnalysisRequiredError

        raise AnalysisRequiredError()

    result: OrientationResult = stored["result"]
    request: OrientationRequest = stored["request"]
    model: MechanicalModel = stored["model"]
    index = min(max(candidate_rank, 1), len(result.candidates)) - 1
    candidate = result.candidates[index]
    settings_result: PrintSettings | None = record.results.get("settings")

    material = result.material
    mass = materials_db.mass_estimate_g(material, record.metrics.volume_mm3)

    return {
        "schema": "print-engineering-optimizer/analysis-report/1",
        "generated_at": time.time(),
        "disclaimer": DISCLAIMER,
        "model": {
            "id": record.id,
            "filename": record.filename,
            "stl_format": record.stl_format,
            "triangle_count": record.metrics.triangle_count,
            "analysis_triangle_count": record.prepared.face_count,
            "dimensions_mm": record.metrics.dimensions,
            "volume_mm3": record.metrics.volume_mm3,
            "surface_area_mm2": record.metrics.surface_area_mm2,
            "center_of_mass": record.metrics.center_of_mass,
            "solid_mass_estimate_g": mass,
            "validation": geometry_report(record).validation,
        },
        "material": material.model_dump(mode="json"),
        "printer": get_printer(request.process.printer_id).model_dump(mode="json"),
        "process": request.process.model_dump(mode="json"),
        "loads": [load.model_dump(mode="json") for load in request.loads],
        "constraints": [c.model_dump(mode="json") for c in request.constraints],
        "optimization": {
            "preset": request.preset.value,
            "weights": result.weights,
            "search": request.search.model_dump(mode="json"),
            "evaluated_candidates": result.evaluated_candidates,
            "duration_seconds": round(result.duration_seconds, 3),
        },
        "selected_orientation": candidate.model_dump(mode="json"),
        "all_candidates": [c.model_dump(mode="json") for c in result.candidates],
        "critical_regions": [r.model_dump(mode="json") for r in result.critical_regions],
        "confidence": result.confidence.model_dump(mode="json"),
        "mechanical_method": {
            "solver": model.solver_name,
            "is_approximation": model.is_approximation,
            "assumptions": model.assumptions,
            "strengths_mpa": model.strengths if model.has_loads else {},
            "warnings": model.warnings,
        },
        "print_settings": settings_result.model_dump(mode="json") if settings_result else None,
        "warnings": result.warnings,
        "limitations": result.confidence.limitations,
    }


def build_export(record: ModelRecord, candidate_rank: int = 1) -> dict[str, Any]:
    """The compact machine-readable export described in the specification."""
    settings_result: PrintSettings | None = record.results.get("settings")
    if settings_result is None:
        from app.core.errors import AnalysisRequiredError

        raise AnalysisRequiredError(
            "Generate the print settings recommendation before exporting."
        )
    stored = record.results["orientation"]
    result: OrientationResult = stored["result"]
    index = min(max(candidate_rank, 1), len(result.candidates)) - 1
    candidate = result.candidates[index]

    return {
        "printer": settings_result.printer,
        "material": settings_result.material,
        "nozzle": settings_result.nozzle,
        "orientation": {
            "x": candidate.rotation_x,
            "y": candidate.rotation_y,
            "z": candidate.rotation_z,
        },
        "layer_height": settings_result.layer_height,
        "first_layer_height": settings_result.first_layer_height,
        "wall_loops": settings_result.wall_loops,
        "top_layers": settings_result.top_layers,
        "bottom_layers": settings_result.bottom_layers,
        "infill_density": settings_result.infill_density,
        "infill_pattern": settings_result.infill_pattern,
        "supports": settings_result.supports,
        "support_angle": settings_result.support_angle,
        "support_type": settings_result.support_type,
        "brim": settings_result.brim,
        "brim_width_mm": settings_result.brim_width_mm,
        "scores": {
            "overall": candidate.overall_score,
            "mechanical": candidate.mechanical_score,
            "layer": candidate.layer_score,
            "support": candidate.support_score,
            "overhang": candidate.overhang_score,
            "stability": candidate.stability_score,
            "warping": candidate.warping_score,
            "material": candidate.material_score,
            "print_time": candidate.print_time_score,
        },
        "analysis_confidence": result.confidence.score,
        "disclaimer": DISCLAIMER,
    }
