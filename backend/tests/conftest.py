from __future__ import annotations

import sys
from pathlib import Path

import pytest
import trimesh

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = BACKEND_ROOT.parent
TEST_DATA = REPO_ROOT / "test-data"


def _ensure_test_data() -> None:
    if (TEST_DATA / "beam.stl").exists():
        return
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import make_test_data  # type: ignore

    TEST_DATA.mkdir(parents=True, exist_ok=True)
    for name, factory in make_test_data.PARTS.items():
        (TEST_DATA / f"{name}.stl").write_bytes(factory().export(file_type="stl"))
    (TEST_DATA / "cube_ascii.stl").write_bytes(
        make_test_data.cube(10.0).export(file_type="stl_ascii").encode("utf-8")
    )


_ensure_test_data()


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    return TEST_DATA


@pytest.fixture
def stl_bytes(test_data_dir):
    def _load(name: str) -> bytes:
        return (test_data_dir / f"{name}.stl").read_bytes()

    return _load


@pytest.fixture
def mesh_of(test_data_dir):
    def _load(name: str) -> trimesh.Trimesh:
        return trimesh.load(test_data_dir / f"{name}.stl", force="mesh")

    return _load


@pytest.fixture(autouse=True)
def _clean_store():
    from app.storage.store import store

    store.clear()
    yield
    store.clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
