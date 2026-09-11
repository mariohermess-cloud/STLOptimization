"""HTTP API.

Every endpoint validates with Pydantic and fails with a stable error code from
:mod:`app.core.errors`. Stack traces never reach the client; they are logged.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import (
    AnalysisRequiredError,
    AppError,
    FileTooLargeError,
    UnsupportedFileError,
)
from app.geometry.regions import pick_region
from app.materials import database as materials_db
from app.printing.printers import all_printers, get_printer
from app.schemas import (
    ExportRequest,
    GeometryReport,
    Job,
    JobStatus,
    Material,
    ModelSummary,
    OrientationRequest,
    OrientationResult,
    Printer,
    PrintSettings,
    RegionPickRequest,
    RegionPickResponse,
    SettingsRequest,
)
from app.services import analysis
from app.storage.jobs import JobRecord, jobs
from app.storage.store import store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

ALLOWED_SUFFIXES = (".stl",)


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------
@router.get("/materials", response_model=list[Material], tags=["catalogue"])
def list_materials() -> list[Material]:
    return materials_db.all_materials()


@router.get("/materials/disclaimer", tags=["catalogue"])
def materials_disclaimer() -> dict[str, str]:
    return {"disclaimer": materials_db.database_disclaimer()}


@router.get("/materials/{material_id}", response_model=Material, tags=["catalogue"])
def get_material(material_id: str) -> Material:
    return materials_db.get_material(material_id)


@router.get("/printers", response_model=list[Printer], tags=["catalogue"])
def list_printers() -> list[Printer]:
    return all_printers()


@router.get("/printers/{printer_id}", response_model=Printer, tags=["catalogue"])
def read_printer(printer_id: str) -> Printer:
    return get_printer(printer_id)


@router.get("/presets", tags=["catalogue"])
def list_presets() -> list[dict]:
    from app.orientation.presets import PRESET_LABELS, PRESET_WEIGHTS
    from app.schemas import OptimizationWeights, Preset

    entries = []
    for preset, label in PRESET_LABELS.items():
        weights = (
            PRESET_WEIGHTS[preset] if preset in PRESET_WEIGHTS else OptimizationWeights()
        )
        entries.append(
            {
                "id": preset.value,
                "label": label,
                "weights": weights.model_dump(),
                "normalized": weights.normalized(),
            }
        )
    return entries


@router.get("/limits", tags=["catalogue"])
def read_limits() -> dict:
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_faces": settings.max_faces,
        "analysis_face_budget": settings.analysis_face_budget,
        "model_ttl_seconds": settings.model_ttl_seconds,
        "accepted_extensions": list(ALLOWED_SUFFIXES),
    }


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
@router.post("/models/upload", response_model=ModelSummary, tags=["models"])
async def upload_model(file: UploadFile = File(...)) -> ModelSummary:
    name = file.filename or ""
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        raise UnsupportedFileError()

    # Read in bounded chunks so an oversized upload is rejected before it is
    # buffered in full.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise FileTooLargeError(
                f"The upload exceeds the limit of {settings.max_upload_bytes / 1e6:.0f} MB."
            )
        chunks.append(chunk)

    data = b"".join(chunks)
    record = analysis.ingest(data, name)
    return analysis.summarize(record)


@router.get("/models/{model_id}", response_model=ModelSummary, tags=["models"])
def get_model(model_id: str) -> ModelSummary:
    return analysis.summarize(store.get(model_id))


@router.get("/models/{model_id}/mesh.stl", tags=["models"])
def get_model_mesh(model_id: str) -> Response:
    """The repaired mesh as binary STL.

    The viewer loads this rather than the original upload so that triangle
    indices match the ones the API expects back for face selection.
    """
    record = store.get(model_id)
    return Response(
        content=record.processed_stl(),
        media_type="model/stl",
        headers={"Content-Disposition": f'inline; filename="{record.id}.stl"'},
    )


@router.post("/models/{model_id}/analyze", response_model=GeometryReport, tags=["analysis"])
def analyze_model(model_id: str, material_id: str | None = Query(default=None)) -> GeometryReport:
    return analysis.geometry_report(store.get(model_id), material_id)


@router.post(
    "/models/{model_id}/pick-region", response_model=RegionPickResponse, tags=["analysis"]
)
def pick_face_region(model_id: str, request: RegionPickRequest) -> RegionPickResponse:
    record = store.get(model_id)
    try:
        result = pick_region(
            record.mesh,
            request.face_id,
            request.angle_tolerance_deg,
            request.max_faces,
        )
    except IndexError as exc:
        raise AppError(str(exc)) from exc
    return RegionPickResponse(**result)


@router.post("/models/{model_id}/loads", tags=["analysis"])
def validate_loads(model_id: str, request: OrientationRequest) -> dict:
    """Validate loads and constraints against the geometry without optimising.

    Returns the resolved load paths so the viewer can draw force arrows at the
    point the solver will actually use.
    """
    record = store.get(model_id)
    model = analysis.build_mechanical_model(record, request)
    return {
        "load_paths": [
            {
                "load_case_id": path.load_case_id,
                "name": path.name,
                "fixed_point": [float(v) for v in path.fixed_point],
                "application_point": [float(v) for v in path.application_point],
                "axis": [float(v) for v in path.axis],
                "lever_length_mm": path.lever_length_mm,
                "force_n": [float(v) for v in path.force_n],
                "torque_nmm": [float(v) for v in path.torque_nmm],
                "type": path.declared_type.value,
                "section_count": len(path.sections),
            }
            for path in model.load_paths
        ],
        "stress_point_count": len(model.points),
        "warnings": model.warnings,
        "assumptions": model.assumptions,
        "strengths_mpa": model.strengths if model.has_loads else {},
    }


# --------------------------------------------------------------------------
# optimisation
# --------------------------------------------------------------------------
@router.post("/models/{model_id}/optimize-orientation", response_model=Job, tags=["optimization"])
def start_optimization(model_id: str, request: OrientationRequest) -> Job:
    """Start the orientation search as a background job.

    The search is CPU bound and can take minutes on a large mesh, so the HTTP
    request returns a job handle immediately. Poll ``/api/jobs/{id}``.
    """
    record = store.get(model_id)

    # Validate the inputs synchronously so the client gets a real error code
    # instead of a failed job.
    if not request.printability_only:
        from app.core.errors import NoConstraintError, NoLoadCaseError

        if not request.loads:
            raise NoLoadCaseError()
        if not any(constraint.face_ids for constraint in request.constraints):
            raise NoConstraintError()
    materials_db.resolve(request.material)
    get_printer(request.process.printer_id)

    job = jobs.submit(
        model_id,
        "optimize_orientation",
        lambda progress: analysis.run_optimization(record, request, progress),
    )
    return _job_to_schema(job)


@router.post(
    "/models/{model_id}/optimize-orientation/sync",
    response_model=OrientationResult,
    tags=["optimization"],
)
def optimize_sync(model_id: str, request: OrientationRequest) -> OrientationResult:
    """Synchronous variant, for small models, tests and scripting."""
    record = store.get(model_id)
    return analysis.run_optimization(record, request)


@router.get("/jobs/{job_id}", response_model=Job, tags=["optimization"])
def get_job(job_id: str) -> Job:
    return _job_to_schema(jobs.get(job_id))


@router.get("/jobs/{job_id}/result", response_model=OrientationResult, tags=["optimization"])
def get_job_result(job_id: str) -> OrientationResult:
    job = jobs.get(job_id)
    if job.status is JobStatus.failed and job.error:
        return JSONResponse(status_code=500, content={"error": job.error})  # type: ignore[return-value]
    if job.status is not JobStatus.completed or job.result is None:
        raise AnalysisRequiredError("The job has not finished yet.")
    return job.result


@router.get("/models/{model_id}/results", response_model=OrientationResult, tags=["optimization"])
def get_results(model_id: str) -> OrientationResult:
    record = store.get(model_id)
    stored = record.results.get("orientation")
    if not stored:
        raise AnalysisRequiredError("No orientation result is stored for this model yet.")
    return stored["result"]


@router.post(
    "/models/{model_id}/recommend-settings", response_model=PrintSettings, tags=["optimization"]
)
def recommend_settings(model_id: str, request: SettingsRequest) -> PrintSettings:
    return analysis.recommend_settings(store.get(model_id), request)


@router.post("/models/{model_id}/export", tags=["export"])
def export_settings(model_id: str, request: ExportRequest) -> dict:
    record = store.get(model_id)
    payload = analysis.build_export(record, request.candidate_rank)
    if request.include_report:
        payload["report"] = analysis.build_report(record, request.candidate_rank)
    return payload


@router.get("/models/{model_id}/report", tags=["export"])
def get_report(model_id: str, candidate_rank: int = Query(default=1, ge=1)) -> dict:
    return analysis.build_report(store.get(model_id), candidate_rank)


# --------------------------------------------------------------------------
def _job_to_schema(job: JobRecord) -> Job:
    return Job(
        id=job.id,
        model_id=job.model_id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        message=job.message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        error=job.error,
        result_available=job.status is JobStatus.completed and job.result is not None,
    )
