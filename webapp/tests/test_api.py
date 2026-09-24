"""FastAPI 层 API 测试。"""
from __future__ import annotations

import time
import unittest

from fastapi.testclient import TestClient

from webapp.app.main import app


def _two_phase_request() -> dict:
    return {
        "intersection": {
            "movements": [
                {"id": "E", "demand": 600},
                {"id": "W", "demand": 600},
            ],
            "phases": [
                {"id": "P1", "capacity": {"E": 1800}},
                {"id": "P2", "capacity": {"W": 1800}},
            ],
            "lost_time": [["P1", "P2", 5], ["P2", "P1", 5]],
            "g_min": 15,
            "c_min": 30,
            "c_max": 180,
        },
        "constraints": [],
        "solver": {"mip_rel_gap": 0.001, "time_limit": 30},
    }


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_validate_ok(self) -> None:
        response = self.client.post("/api/validate", json=_two_phase_request())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["phase_count"], 2)

    def test_validate_bad(self) -> None:
        payload = _two_phase_request()
        payload["intersection"]["movements"].append({"id": "N", "demand": 300})
        response = self.client.post("/api/validate", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["errors"])

    def test_validate_rejects_unregistered_variable(self) -> None:
        payload = _two_phase_request()
        payload["constraints"] = [
            {
                "name": "南北直行行人过街时间",
                "coeffs": [["g", "", 1.0]],
                "sense": ">=",
                "rhs": 30.0,
                "trigger": {"kind": "always", "phases": []},
            }
        ]
        response = self.client.post("/api/validate", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["errors"][0]["code"], "CONSTRAINT_INVALID")

    def test_audit_trap(self) -> None:
        payload = {
            "intersection": _two_phase_request()["intersection"],
            "constraint": {
                "name": "恒违反示例",
                "coeffs": [["g", "P1", 1.0]],
                "sense": ">=",
                "rhs": 200.0,
                "trigger": {"kind": "always", "phases": []},
            },
        }
        response = self.client.post("/api/audit", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["report"]["trap_subsets"])

    def test_optimize_lifecycle(self) -> None:
        response = self.client.post("/api/optimize", json=_two_phase_request())
        self.assertEqual(response.status_code, 202)
        task_id = response.json()["task_id"]

        task = None
        for _ in range(100):
            task_response = self.client.get(f"/api/tasks/{task_id}")
            self.assertEqual(task_response.status_code, 200)
            task = task_response.json()
            if task["status"] in {"succeeded", "failed", "infeasible", "cancelled"}:
                break
            time.sleep(0.05)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "succeeded", task)

        result_response = self.client.get(f"/api/tasks/{task_id}/result")
        self.assertEqual(result_response.status_code, 200)
        result = result_response.json()["result"]
        self.assertAlmostEqual(result["cycle"], 40.0, places=4)
        self.assertEqual(result["selected"], ["P1", "P2"])
        self.assertTrue(result["lane_balance"])


if __name__ == "__main__":
    unittest.main()
