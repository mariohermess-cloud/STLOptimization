"""Application configuration.

All tunable limits live here so that deployment can override them through
environment variables (prefix ``PEO_``) without touching code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PEO_", env_file=".env", extra="ignore")

    app_name: str = "Print Engineering Optimizer"
    version: str = "0.1.0"

    # --- storage -----------------------------------------------------------
    storage_dir: Path = Path("/tmp/peo-storage")
    #: Hard upload limit. STL files above this are rejected before parsing.
    max_upload_bytes: int = 150 * 1024 * 1024
    #: Models older than this are removed by the janitor task.
    model_ttl_seconds: int = 6 * 3600
    #: Upper bound on how many models are kept in memory at once.
    max_models: int = 64

    # --- mesh limits -------------------------------------------------------
    #: Meshes larger than this are decimated for the analysis pipeline.
    #: The full-resolution mesh is kept for display and measurement.
    analysis_face_budget: int = 60_000
    #: Absolute refusal limit - protects the server from pathological files.
    max_faces: int = 6_000_000

    # --- optimisation ------------------------------------------------------
    #: Worker threads used to evaluate orientation candidates.
    optimizer_workers: int = 4
    #: Wall-clock budget for a single orientation optimisation job.
    optimizer_timeout_seconds: float = 600.0

    # --- api ---------------------------------------------------------------
    cors_origins: list[str] = ["*"]
    log_level: str = "INFO"


settings = Settings()
