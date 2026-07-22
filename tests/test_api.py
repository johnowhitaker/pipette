from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from pixel_pipette.main import create_app


class PipetteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        app = create_app(
            database_path=Path(self.temp_dir.name) / "api.sqlite3",
            hardware_mode="simulate",
            admin_password="test-password",
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def login(self) -> None:
        response = self.client.post("/api/admin/login", json={"password": "test-password"})
        self.assertEqual(response.status_code, 200)

    def calibrate_red(self) -> None:
        state = self.client.get("/api/admin/state").json()
        config = state["config"]
        config["paper"] = {"bottom_right_x": 80, "bottom_right_y": 80, "deposit_z": 0}
        config["motion"]["travel_z"] = 30
        config["servo"].update(
            {"rest_position": 1100, "draw_position": 1060, "purge_position": 1010, "settle_ms": 50}
        )
        response = self.client.put("/api/admin/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)

        red = next(color for color in state["colors"] if color["id"] == "red")
        red.update({"intake_z": 6, "purge_z": 25})
        payload = {
            key: red[key]
            for key in ("name", "hex", "x", "y", "intake_z", "purge_z", "enabled")
        }
        response = self.client.put("/api/admin/colors/red", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def test_public_pages_and_admin_auth(self) -> None:
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/admin").status_code, 200)
        public = self.client.get("/api/public/config").json()
        self.assertEqual(public["grid_size"], 8)
        self.assertEqual(len(public["colors"]), 3)
        self.assertEqual(self.client.get("/api/admin/state").status_code, 401)
        self.assertEqual(
            self.client.post("/api/admin/login", json={"password": "wrong"}).status_code,
            401,
        )
        self.login()
        self.assertEqual(self.client.get("/api/admin/state").status_code, 200)

    def test_submission_to_completed_simulated_print(self) -> None:
        self.login()
        self.calibrate_red()

        jog = self.client.post(
            "/api/admin/hardware/jog", json={"axis": "X", "distance": 1}
        )
        self.assertEqual(jog.status_code, 200, jog.text)
        self.assertEqual(jog.json()["x"], 1)

        pixels = ["red"] + [None] * 63
        submitted = self.client.post(
            "/api/submissions",
            json={"artist_name": "API test", "grid_size": 8, "pixels": pixels},
        )
        self.assertEqual(submitted.status_code, 201, submitted.text)
        job_id = submitted.json()["id"]
        started = self.client.post("/api/admin/jobs/print-next", json={})
        self.assertEqual(started.status_code, 200, started.text)

        job = None
        for _ in range(30):
            time.sleep(0.05)
            jobs = self.client.get("/api/admin/state").json()["jobs"]
            job = next(item for item in jobs if item["id"] == job_id)
            if job["status"] != "printing":
                break
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["progress_current"], 1)

    def test_empty_and_unknown_color_submissions_are_rejected(self) -> None:
        empty = self.client.post(
            "/api/submissions",
            json={"artist_name": "", "grid_size": 8, "pixels": [None] * 64},
        )
        self.assertEqual(empty.status_code, 422)
        unknown = self.client.post(
            "/api/submissions",
            json={"artist_name": "", "grid_size": 8, "pixels": ["chartreuse"] + [None] * 63},
        )
        self.assertEqual(unknown.status_code, 422)


if __name__ == "__main__":
    unittest.main()
