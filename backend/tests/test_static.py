"""Serving the built web UI from the API process (the portable build path)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.static import mount_web_ui, static_directory


@pytest.fixture
def bundle(tmp_path):
    """A minimal stand-in for the built frontend."""
    (tmp_path / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return tmp_path


@pytest.fixture
def web_client(bundle):
    app = FastAPI()

    @app.get("/api/printers")
    def printers() -> list[str]:
        return ["bambu_x1c"]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    mount_web_ui(app, bundle)
    with TestClient(app) as client:
        yield client


class TestStaticDirectory:
    def test_unset_means_no_web_ui(self, monkeypatch):
        monkeypatch.delenv("PEO_STATIC_DIR", raising=False)
        assert static_directory() is None

    def test_directory_without_index_is_refused(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PEO_STATIC_DIR", str(tmp_path))
        assert static_directory() is None

    def test_directory_with_index_is_accepted(self, monkeypatch, bundle):
        monkeypatch.setenv("PEO_STATIC_DIR", str(bundle))
        assert static_directory() == bundle


class TestServing:
    def test_root_serves_the_shell(self, web_client):
        response = web_client.get("/")
        assert response.status_code == 200
        assert "SPA" in response.text

    def test_the_shell_is_not_cached(self, web_client):
        # A cached index.html keeps loading the previous bundle's asset names
        # after an update.
        assert web_client.get("/").headers["cache-control"] == "no-store"

    def test_assets_are_served(self, web_client):
        response = web_client.get("/assets/index-abc123.js")
        assert response.status_code == 200
        assert response.text == "console.log(1)"

    def test_a_real_file_is_served_as_itself(self, web_client):
        assert web_client.get("/favicon.svg").text == "<svg/>"

    def test_unknown_route_falls_back_to_the_shell(self, web_client):
        response = web_client.get("/orientation/3")
        assert response.status_code == 200
        assert "SPA" in response.text


class TestApiIsNotShadowed:
    """The SPA catch-all must not swallow the API."""

    def test_api_route_still_works(self, web_client):
        assert web_client.get("/api/printers").json() == ["bambu_x1c"]

    def test_health_still_works(self, web_client):
        assert web_client.get("/health").json() == {"status": "ok"}

    @pytest.mark.parametrize("path", ["/api/nope", "/api/models/x/does-not-exist"])
    def test_unknown_api_paths_return_404_not_the_shell(self, web_client, path):
        # Without the prefix guard these would fall through to the SPA and
        # answer 200 with HTML, which would make a mistyped endpoint look like
        # a working page to a client.
        response = web_client.get(path)
        assert response.status_code == 404
        assert "SPA" not in response.text

    @pytest.mark.parametrize("path", ["/openapi.json", "/docs"])
    def test_the_api_documentation_is_not_shadowed(self, web_client, path):
        # These exist in FastAPI, so 200 is correct - what matters is that the
        # SPA catch-all did not answer in their place.
        response = web_client.get(path)
        assert response.status_code == 200
        assert "SPA" not in response.text


class TestContainment:
    def test_a_path_cannot_escape_the_bundle(self, web_client, tmp_path):
        secret = tmp_path.parent / "outside-the-bundle.txt"
        secret.write_text("do not serve me", encoding="utf-8")

        response = web_client.get(f"/../{secret.name}")
        assert "do not serve me" not in response.text

    @pytest.mark.parametrize(
        "path",
        [
            "/../../etc/passwd",
            "/..%2f..%2fetc%2fpasswd",
            "/assets/../../etc/passwd",
        ],
    )
    def test_traversal_attempts_never_return_file_contents(self, web_client, path):
        response = web_client.get(path)
        # Either refused, or answered with the SPA shell - never the target.
        assert response.status_code in (200, 404)
        assert "root:" not in response.text
