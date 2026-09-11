"""API contract and error handling tests."""

from __future__ import annotations

import time

import pytest


def upload(client, stl_bytes, name="bracket"):
    response = client.post(
        "/api/models/upload", files={"file": (f"{name}.stl", stl_bytes(name), "model/stl")}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def beam_boundary(client, model_id):
    """Fixed faces at one end of the beam, loaded faces at the other."""
    import trimesh

    mesh = trimesh.load(
        trimesh.util.wrap_as_stream(client.get(f"/api/models/{model_id}/mesh.stl").content),
        file_type="stl",
        force="mesh",
    )
    centers = mesh.triangles_center
    fixed = [i for i, c in enumerate(centers) if c[0] < -55]
    loaded = [i for i, c in enumerate(centers) if c[0] > 55]
    return fixed, loaded


class TestCatalogue:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"

    def test_materials_include_every_specified_family(self, client):
        ids = {material["id"] for material in client.get("/api/materials").json()}
        expected = {
            "pla", "pla-cf", "petg", "petg-cf", "abs", "asa", "asa-cf",
            "pa", "pa-cf", "pc", "tpu",
        }
        assert expected <= ids

    def test_material_disclaimer_is_served(self, client):
        assert "verify" in client.get("/api/materials/disclaimer").json()["disclaimer"].lower()

    def test_unknown_material(self, client):
        response = client.get("/api/materials/unobtainium")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "material_not_found"

    def test_printers(self, client):
        ids = {printer["id"] for printer in client.get("/api/printers").json()}
        assert "bambu_x1c" in ids

    def test_presets_expose_their_weights(self, client):
        presets = client.get("/api/presets").json()
        assert {preset["id"] for preset in presets} >= {
            "max_strength", "balanced", "fast", "lightweight"
        }
        for preset in presets:
            assert sum(preset["normalized"].values()) == pytest.approx(1.0)

    def test_limits(self, client):
        limits = client.get("/api/limits").json()
        assert limits["max_upload_bytes"] > 0
        assert ".stl" in limits["accepted_extensions"]


class TestUpload:
    def test_upload_returns_a_summary(self, client, stl_bytes):
        body = client.post(
            "/api/models/upload", files={"file": ("bracket.stl", stl_bytes("bracket"))}
        ).json()
        assert body["triangle_count"] > 0
        assert body["stl_format"] == "binary"
        assert body["decimated"] is False

    def test_ascii_upload(self, client, test_data_dir):
        data = (test_data_dir / "cube_ascii.stl").read_bytes()
        body = client.post("/api/models/upload", files={"file": ("c.stl", data)}).json()
        assert body["stl_format"] == "ascii"

    def test_duplicate_upload_is_deduplicated(self, client, stl_bytes):
        first = upload(client, stl_bytes)
        second = client.post(
            "/api/models/upload", files={"file": ("renamed.stl", stl_bytes("bracket"))}
        ).json()["id"]
        assert first == second

    def test_wrong_extension_is_rejected(self, client):
        response = client.post("/api/models/upload", files={"file": ("m.obj", b"v 0 0 0")})
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_file"

    def test_corrupt_stl_gives_a_readable_error(self, client):
        response = client.post("/api/models/upload", files={"file": ("m.stl", b"garbage" * 50)})
        assert response.status_code in (415, 422)
        message = response.json()["error"]["message"]
        assert "Traceback" not in message and len(message) > 10

    def test_missing_file_field(self, client):
        response = client.post("/api/models/upload", json={})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_unknown_model(self, client):
        response = client.get("/api/models/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "model_not_found"

    def test_processed_mesh_is_served_as_binary_stl(self, client, stl_bytes):
        model_id = upload(client, stl_bytes)
        response = client.get(f"/api/models/{model_id}/mesh.stl")
        assert response.status_code == 200
        assert len(response.content) > 84


class TestAnalysis:
    def test_geometry_report(self, client, stl_bytes):
        model_id = upload(client, stl_bytes, "cube")
        body = client.post(f"/api/models/{model_id}/analyze?material_id=pla").json()
        assert body["metrics"]["volume_mm3"] == pytest.approx(8000.0, rel=1e-6)
        assert body["validation"]["is_watertight"]
        assert body["mass_estimate_g"] == pytest.approx(8.0 * 1.24, rel=1e-6)
        assert body["mass_material"] == "PLA"

    def test_pick_region(self, client, stl_bytes):
        model_id = upload(client, stl_bytes, "cube")
        body = client.post(
            f"/api/models/{model_id}/pick-region", json={"face_id": 0, "angle_tolerance_deg": 5}
        ).json()
        assert body["area_mm2"] == pytest.approx(400.0)
        assert body["is_planar"]

    def test_pick_region_out_of_range(self, client, stl_bytes):
        model_id = upload(client, stl_bytes, "cube")
        response = client.post(f"/api/models/{model_id}/pick-region", json={"face_id": 99999})
        assert response.status_code == 500
        assert "Traceback" not in response.json()["error"]["message"]

    def test_load_validation_returns_resolved_paths(self, client, stl_bytes):
        model_id = upload(client, stl_bytes, "beam")
        fixed, loaded = beam_boundary(client, model_id)
        body = client.post(
            f"/api/models/{model_id}/loads",
            json={
                "material": {"material_id": "petg"},
                "loads": [
                    {
                        "id": "l1", "name": "Tip", "type": "bending",
                        "force_n": [0, 0, -200], "application_face_ids": loaded,
                    }
                ],
                "constraints": [{"face_ids": fixed}],
            },
        ).json()
        assert len(body["load_paths"]) == 1
        path = body["load_paths"][0]
        assert path["lever_length_mm"] == pytest.approx(120.0, rel=0.02)
        assert path["section_count"] > 0
        assert body["stress_point_count"] > 0


class TestOptimization:
    def test_rejects_missing_load_case(self, client, stl_bytes):
        model_id = upload(client, stl_bytes)
        response = client.post(
            f"/api/models/{model_id}/optimize-orientation",
            json={"material": {"material_id": "petg"}},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "no_load_case"

    def test_rejects_missing_constraint(self, client, stl_bytes):
        model_id = upload(client, stl_bytes)
        response = client.post(
            f"/api/models/{model_id}/optimize-orientation",
            json={
                "material": {"material_id": "petg"},
                "loads": [{"id": "l", "name": "l", "type": "custom", "force_n": [0, 0, -10]}],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "no_constraint"

    def test_rejects_a_load_with_no_magnitude(self, client, stl_bytes):
        model_id = upload(client, stl_bytes)
        response = client.post(
            f"/api/models/{model_id}/optimize-orientation",
            json={
                "material": {"material_id": "petg"},
                "loads": [{"id": "l", "name": "l", "type": "custom", "force_n": [0, 0, 0]}],
            },
        )
        assert response.status_code == 422

    def test_printability_only_runs_without_loads(self, client, stl_bytes):
        model_id = upload(client, stl_bytes)
        response = client.post(
            f"/api/models/{model_id}/optimize-orientation/sync",
            json={
                "material": {"material_id": "petg"},
                "printability_only": True,
                "search": {"coarse_step_deg": 30, "enable_fine_stage": False},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["weights"]["mechanical"] == 0.0
        assert any("no mechanical load" in w.lower() for w in body["warnings"])

    def test_background_job_completes_and_returns_a_result(self, client, stl_bytes):
        model_id = upload(client, stl_bytes, "beam")
        fixed, loaded = beam_boundary(client, model_id)
        payload = {
            "material": {"material_id": "petg"},
            "loads": [
                {"id": "l1", "name": "Tip", "type": "bending",
                 "force_n": [0, 0, -200], "application_face_ids": loaded}
            ],
            "constraints": [{"face_ids": fixed}],
            "preset": "max_strength",
            "search": {"coarse_step_deg": 30, "enable_fine_stage": False},
        }
        job = client.post(f"/api/models/{model_id}/optimize-orientation", json=payload).json()
        assert job["status"] in ("queued", "running", "completed")

        deadline = time.time() + 120
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert job["status"] == "completed", job
        assert job["progress"] == 1.0

        result = client.get(f"/api/jobs/{job['id']}/result").json()
        assert 1 <= len(result["candidates"]) <= 5
        assert result["candidates"][0]["rank"] == 1
        assert result["confidence"]["score"] > 0
        assert result["disclaimer"]

        # The stored result is retrievable by model as well.
        stored = client.get(f"/api/models/{model_id}/results").json()
        assert stored["candidates"][0]["overall_score"] == result["candidates"][0]["overall_score"]

    def test_unknown_job(self, client):
        assert client.get("/api/jobs/nope").status_code == 404

    def test_failed_job_reports_an_error_instead_of_a_result(
        self, client, stl_bytes, monkeypatch
    ):
        from app.core.errors import OptimizationFailedError
        from app.services import analysis as analysis_module

        def boom(*args, **kwargs):
            raise OptimizationFailedError("The orientation search could not complete.")

        monkeypatch.setattr(analysis_module, "run_optimization", boom)

        model_id = upload(client, stl_bytes)
        job = client.post(
            f"/api/models/{model_id}/optimize-orientation",
            json={
                "material": {"material_id": "petg"},
                "printability_only": True,
                "search": {"coarse_step_deg": 40, "enable_fine_stage": False},
            },
        ).json()

        deadline = time.time() + 60
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job['id']}").json()
            if job["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)
        assert job["status"] == "failed"
        assert job["error"]["code"] == "optimization_failed"
        assert job["result_available"] is False

        response = client.get(f"/api/jobs/{job['id']}/result")
        assert response.status_code >= 400
        body = response.json()
        assert "Traceback" not in body["error"]["message"]

    def test_results_before_analysis(self, client, stl_bytes):
        model_id = upload(client, stl_bytes)
        response = client.get(f"/api/models/{model_id}/results")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "analysis_required"


class TestScoreTransparency:
    @pytest.fixture
    def analysed(self, client, stl_bytes):
        model_id = upload(client, stl_bytes, "beam")
        fixed, loaded = beam_boundary(client, model_id)
        client.post(
            f"/api/models/{model_id}/optimize-orientation/sync",
            json={
                "material": {"material_id": "petg"},
                "loads": [
                    {"id": "l1", "name": "Tip", "type": "bending",
                     "force_n": [0, 0, -200], "application_face_ids": loaded}
                ],
                "constraints": [{"face_ids": fixed}],
                "search": {"coarse_step_deg": 30, "enable_fine_stage": False},
            },
        )
        return model_id

    def test_overall_score_is_reproducible_from_the_components(self, client, analysed):
        result = client.get(f"/api/models/{analysed}/results").json()
        for candidate in result["candidates"]:
            total = sum(component["contribution"] for component in candidate["components"])
            assert candidate["overall_score"] == pytest.approx(total, abs=0.1)
            keys = {component["key"] for component in candidate["components"]}
            assert keys == {
                "mechanical", "layer", "support", "overhang",
                "stability", "warping", "material", "print_time",
            }

    def test_every_candidate_explains_itself(self, client, analysed):
        result = client.get(f"/api/models/{analysed}/results").json()
        for candidate in result["candidates"]:
            assert candidate["reasons"], "an orientation without a justification is a black box"

    def test_confidence_factors_add_up(self, client, analysed):
        confidence = client.get(f"/api/models/{analysed}/results").json()["confidence"]
        total_weight = sum(factor["weight"] for factor in confidence["factors"])
        expected = (
            sum(factor["score"] * factor["weight"] for factor in confidence["factors"])
            / total_weight
        )
        assert confidence["score"] == pytest.approx(expected, abs=0.1)
        assert confidence["limitations"]
        assert any("not a certified" in item for item in confidence["limitations"])

    def test_settings_and_export(self, client, analysed):
        settings = client.post(
            f"/api/models/{analysed}/recommend-settings",
            json={"material": {"material_id": "petg"}, "preset": "max_strength"},
        ).json()
        assert settings["wall_loops"] >= 2
        assert settings["reasons"]

        export = client.post(
            f"/api/models/{analysed}/export", json={"candidate_rank": 1, "include_report": True}
        ).json()
        for key in (
            "printer", "material", "nozzle", "orientation", "layer_height", "wall_loops",
            "top_layers", "bottom_layers", "infill_density", "infill_pattern",
            "supports", "support_angle", "brim",
        ):
            assert key in export, key
        assert export["layer_height"] == settings["layer_height"]
        assert export["wall_loops"] == settings["wall_loops"]

        report = export["report"]
        for key in (
            "model", "material", "loads", "constraints", "selected_orientation",
            "critical_regions", "confidence", "mechanical_method", "print_settings",
            "limitations",
        ):
            assert key in report, key
        assert report["mechanical_method"]["is_approximation"] is True

    def test_export_before_settings_is_refused(self, client, analysed):
        response = client.post(f"/api/models/{analysed}/export", json={})
        assert response.status_code == 409

    def test_report_endpoint(self, client, analysed):
        report = client.get(f"/api/models/{analysed}/report").json()
        assert report["schema"].startswith("print-engineering-optimizer/")
        assert report["disclaimer"]
