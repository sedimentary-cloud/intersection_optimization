"""适配层契约测试（不需要启动 HTTP 服务）。"""
from __future__ import annotations

import unittest

from webapp.adapter.audit import audit_request
from webapp.adapter.runner import run_optimize_request, validate_request
from webapp.adapter.schemas import AuditRequest, OptimizeRequest


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


class AdapterContractTests(unittest.TestCase):
    def test_validate_ok(self) -> None:
        request = OptimizeRequest.model_validate(_two_phase_request())
        outcome = validate_request(request)
        self.assertTrue(outcome.ok, outcome.errors)
        self.assertEqual(len(outcome.data.movements), 2)
        self.assertEqual(outcome.data.l_bar, 5.0)

    def test_validate_bad_coverage(self) -> None:
        payload = _two_phase_request()
        payload["intersection"]["phases"] = [{"id": "P1", "capacity": {"E": 1800}}]
        request = OptimizeRequest.model_validate(payload)
        outcome = validate_request(request)
        self.assertFalse(outcome.ok)
        self.assertTrue(any("W" in error.message for error in outcome.errors))

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
        report = audit_request(AuditRequest.model_validate(payload))
        self.assertTrue(report["trap_subsets"])
        self.assertTrue(any(entry["trap"] for entry in report["entries"]))

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
        request = OptimizeRequest.model_validate(payload)
        outcome = validate_request(request)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.errors[0].code, "CONSTRAINT_INVALID")
        self.assertIn("未注册变量", outcome.errors[0].message)

    def test_run_optimize(self) -> None:
        request = OptimizeRequest.model_validate(_two_phase_request())
        data, result = run_optimize_request(request)
        self.assertAlmostEqual(result.cycle, 40.0, places=4)
        self.assertEqual(result.selected, ["P1", "P2"])
        self.assertIsNotNone(result.order)
        self.assertIn(data.movement_ids, [["E", "W"], ["W", "E"]])


if __name__ == "__main__":
    unittest.main()
