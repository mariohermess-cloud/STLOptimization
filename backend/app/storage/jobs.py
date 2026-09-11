"""Minimal job abstraction for long running analyses.

Orientation optimisation on a large mesh takes seconds to minutes, which is
too long to hold an HTTP request open. The API therefore starts a job and the
client polls its status.

This is a deliberately small abstraction: an in-process thread pool plus a job
registry, with the same interface a Celery or RQ backend would expose
(``submit`` returning a job id, ``get`` returning status and result). Redis and
a broker are not justified for a single-container MVP; when they are, only
this module changes.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.errors import AppError, JobNotFoundError
from app.schemas import JobStatus

logger = logging.getLogger(__name__)

MAX_TRACKED_JOBS = 200


@dataclass
class JobRecord:
    id: str
    model_id: str
    kind: str
    status: JobStatus = JobStatus.queued
    progress: float = 0.0
    message: str = "Queued"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Any = None
    error: dict[str, Any] | None = None
    future: Future | None = None


class JobManager:
    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="peo-job")
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        model_id: str,
        kind: str,
        function: Callable[[Callable[[float, str], None]], Any],
    ) -> JobRecord:
        job = JobRecord(id=uuid.uuid4().hex, model_id=model_id, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()

        def progress(fraction: float, message: str) -> None:
            job.progress = float(max(0.0, min(1.0, fraction)))
            job.message = message
            job.updated_at = time.time()

        def run() -> Any:
            job.status = JobStatus.running
            job.message = "Running"
            job.updated_at = time.time()
            started = time.perf_counter()
            try:
                result = function(progress)
            except AppError as exc:
                job.status = JobStatus.failed
                job.error = exc.to_payload()["error"]
                job.message = exc.message
                job.updated_at = time.time()
                logger.warning(
                    "job failed",
                    extra={
                        "operation": kind,
                        "model_id": model_id,
                        "job_id": job.id,
                        "code": exc.code,
                    },
                )
                raise
            except Exception as exc:  # noqa: BLE001
                job.status = JobStatus.failed
                job.error = {
                    "code": "internal_error",
                    "message": "The analysis failed unexpectedly. The details were logged.",
                    "details": {},
                }
                job.message = "Failed"
                job.updated_at = time.time()
                logger.exception(
                    "job crashed",
                    extra={"operation": kind, "model_id": model_id, "job_id": job.id},
                )
                raise exc
            job.result = result
            job.status = JobStatus.completed
            job.progress = 1.0
            job.message = "Completed"
            job.updated_at = time.time()
            logger.info(
                "job completed",
                extra={
                    "operation": kind,
                    "model_id": model_id,
                    "job_id": job.id,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            return result

        job.future = self._pool.submit(run)
        return job

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError()
        return job

    def _trim(self) -> None:
        if len(self._jobs) <= MAX_TRACKED_JOBS:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.status in (JobStatus.completed, JobStatus.failed)),
            key=lambda job: job.updated_at,
        )
        for job in finished[: len(self._jobs) - MAX_TRACKED_JOBS]:
            self._jobs.pop(job.id, None)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


jobs = JobManager()
