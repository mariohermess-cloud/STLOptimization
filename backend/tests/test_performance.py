"""Performance benchmark on a large mesh.

Marked ``slow`` and skipped by default; run with::

    pytest -m slow -s

It measures and prints the timings of every pipeline stage so a regression
shows up as a number, and asserts only loose ceilings so the suite does not
fail on a slower machine.
"""

from __future__ import annotations

import time

import pytest
import trimesh

from app.core.config import settings
from app.geometry.prepared import prepare
from app.materials import database as materials_db
from app.mechanics.solver import ApproximateMechanicalSolver
from app.orientation.optimizer import OrientationOptimizer
from app.printing.printers import get_printer
from app.schemas import (
    Constraint,
    LoadCase,
    LoadType,
    MaterialSelection,
    OptimizationWeights,
    OrientationRequest,
    ProcessConfig,
    SearchConfig,
)
from app.services import analysis

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def large_stl() -> bytes:
    """~1.3 M triangles - larger than any realistic bracket, on purpose."""
    mesh = trimesh.creation.icosphere(subdivisions=6, radius=40.0).subdivide().subdivide()
    return mesh.export(file_type="stl")


def test_large_model_pipeline(large_stl, capsys):
    timings: dict[str, float] = {}

    started = time.perf_counter()
    record = analysis.ingest(large_stl, "large.stl")
    timings["upload + parse + repair + metrics"] = time.perf_counter() - started

    started = time.perf_counter()
    analysis.geometry_report(record, "petg")
    timings["geometry report"] = time.perf_counter() - started

    centers = record.mesh.triangles_center
    fixed = [i for i, c in enumerate(centers) if c[2] < -38]
    loaded = [i for i, c in enumerate(centers) if c[2] > 38]

    material = materials_db.get_material("petg")
    started = time.perf_counter()
    model = ApproximateMechanicalSolver().analyze(
        record.mesh,
        material,
        [
            LoadCase(
                id="l", name="Top load", type=LoadType.compression,
                force_n=[0, 0, -500], application_face_ids=loaded,
            )
        ],
        [Constraint(face_ids=fixed)],
    )
    timings["mechanical model (section sweep)"] = time.perf_counter() - started

    started = time.perf_counter()
    prepared = prepare(record.analysis_mesh)
    timings["prepare analysis mesh"] = time.perf_counter() - started

    optimizer = OrientationOptimizer(
        prepared=prepared,
        material=material,
        mechanical_model=model,
        process=ProcessConfig(),
        printer=get_printer("bambu_x1c"),
        weights=OptimizationWeights(),
        search_config=SearchConfig(coarse_step_deg=15.0),
    )
    started = time.perf_counter()
    outcome = optimizer.optimize()
    timings["orientation search (3 stages, 15/5/1 deg)"] = time.perf_counter() - started

    started = time.perf_counter()
    analysis.detect_critical_regions(
        record,
        OrientationRequest(material=MaterialSelection(material_id="petg"), printability_only=True),
        outcome.candidates[0],
    )
    timings["critical region detection"] = time.perf_counter() - started

    with capsys.disabled():
        print("\n--- performance ---")
        print(f"source triangles      : {record.metrics.triangle_count:,}")
        print(f"analysis triangles    : {prepared.face_count:,} (decimated={record.decimated})")
        print(f"orientations evaluated: {outcome.evaluated:,}")
        for name, seconds in timings.items():
            print(f"{name:<42s}: {seconds:7.2f} s")
        print(f"{'TOTAL':<42s}: {sum(timings.values()):7.2f} s")

    assert record.metrics.triangle_count > 1_000_000
    assert prepared.face_count <= settings.analysis_face_budget
    assert outcome.candidates
    # Loose ceilings: these catch an algorithmic regression, not a slow runner.
    assert timings["orientation search (3 stages, 15/5/1 deg)"] < 180.0
    assert timings["upload + parse + repair + metrics"] < 180.0
