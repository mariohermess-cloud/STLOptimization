"""User facing error types.

The API never leaks a stack trace. Anything raised as an ``AppError`` carries a
stable machine readable ``code`` plus a message written for an engineer using
the tool, not for the developer maintaining it. Unexpected exceptions are
logged server side and reported as a generic internal error.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "internal_error"
    status_code = 500
    message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class FileTooLargeError(AppError):
    code = "file_too_large"
    status_code = 413
    message = "The uploaded file exceeds the size limit."


class UnsupportedFileError(AppError):
    code = "unsupported_file"
    status_code = 415
    message = "Only .stl files (binary or ASCII) are supported."


class InvalidMeshError(AppError):
    code = "invalid_stl"
    status_code = 422
    message = "The file could not be read as a valid STL mesh."


class MeshNotWatertightError(AppError):
    code = "mesh_not_watertight"
    status_code = 422
    message = "The mesh is not watertight and could not be repaired automatically."


class ModelNotFoundError(AppError):
    code = "model_not_found"
    status_code = 404
    message = "The model was not found. It may have expired - please upload it again."


class JobNotFoundError(AppError):
    code = "job_not_found"
    status_code = 404
    message = "The job was not found."


class MaterialNotFoundError(AppError):
    code = "material_not_found"
    status_code = 404
    message = "The requested material is not in the material database."


class PrinterNotFoundError(AppError):
    code = "printer_not_found"
    status_code = 404
    message = "The requested printer profile is not available."


class NoLoadCaseError(AppError):
    code = "no_load_case"
    status_code = 422
    message = (
        "No load case was defined. Add at least one load, or enable "
        "'printability only' to optimise without a mechanical objective."
    )


class NoConstraintError(AppError):
    code = "no_constraint"
    status_code = 422
    message = (
        "No fixed surface was selected. Select at least one face that is held "
        "in place, or enable 'printability only'."
    )


class OptimizationFailedError(AppError):
    code = "optimization_failed"
    status_code = 500
    message = "Orientation optimisation failed."


class AnalysisRequiredError(AppError):
    code = "analysis_required"
    status_code = 409
    message = "Run the geometry analysis for this model first."
