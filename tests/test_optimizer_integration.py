from __future__ import annotations

import unittest
import warnings
from unittest.mock import patch

from signal_timing import (
    ConstraintAuditError,
    ConstraintError,
    ConstraintSpec,
    InfeasibleError,
    IntersectionData,
    LexicographicOptimizer,
    Movement,
    OrderingPostProcessor,
    Phase,
    Trigger,
)
from tests.helpers import three_phase_asym_data, two_phase_data


class TestOptimizerIntegration(unittest.TestCase):
    def _opt(self, data, **kw):
        kw.setdefault("mip_rel_gap", 0.001)
        return LexicographicOptimizer(data, **kw)

    # ------------------------------------------------------------------ #
    # 端到端基线
    # ------------------------------------------------------------------ #
    def test_hand_two_phase_free_mode(self):
        data = two_phase_data(demand_e=600, demand_w=600, capacity_e=1800, capacity_w=1800)
        opt = self._opt(data)
        result = opt.solve(allow_cycle_reduction=False)
        self.assertAlmostEqual(result.cycle, 40.0, places=4)
        self.assertEqual(result.selected, ["P1", "P2"])
        self.assertAlmostEqual(result.greens["P1"], 15.0, places=4)
        self.assertAlmostEqual(result.greens["P2"], 15.0, places=4)
        self.assertAlmostEqual(result.waste, 6000.0, delta=1e-3)
        self.assertEqual(result.phase_count, 2)
        self.assertTrue(result.verification["passed"])
        # 服务能力约束在 C=40 时每个流向恰好有 3000 veh/h*s 余量
        self.assertAlmostEqual(result.verification["service_margins"]["E"], 3000.0, delta=1e-3)

    def test_fixed_cycle_mode_consistency(self):
        data = two_phase_data(demand_e=600, demand_w=600)
        free = self._opt(data).solve(allow_cycle_reduction=False)
        fixed = self._opt(data).solve(fixed_cycle=free.cycle)
        self.assertAlmostEqual(fixed.cycle, free.cycle, places=4)
        self.assertEqual(fixed.selected, free.selected)
        self.assertEqual(fixed.phase_count, free.phase_count)
        self.assertAlmostEqual(fixed.waste, free.waste, delta=1e-3)
        self.assertEqual(fixed.order, free.order)
        self.assertTrue(fixed.verification["passed"])

    def test_run_ordering_false(self):
        data = two_phase_data(demand_e=600, demand_w=600)
        result = self._opt(data).solve(run_ordering=False)
        self.assertIsNone(result.order)
        self.assertEqual(result.selected, ["P1", "P2"])
        self.assertTrue(result.verification["passed"])

    # ------------------------------------------------------------------ #
    # §9.5 周期回收
    # ------------------------------------------------------------------ #
    def test_cycle_reduction_recovers_conservatism(self):
        data = three_phase_asym_data(low=2.0, high=20.0)
        no_reduce = self._opt(data).solve(allow_cycle_reduction=False)
        reduce = self._opt(data).solve(allow_cycle_reduction=True)
        self.assertAlmostEqual(no_reduce.cycle, no_reduce.stage1_cycle, places=4)
        self.assertLess(reduce.cycle, reduce.stage1_cycle)
        self.assertAlmostEqual(reduce.cycle, 90.0, places=4)
        self.assertEqual(reduce.order, ["P1", "P2", "P3"])
        self.assertTrue(reduce.verification["passed"])
        # 定点周期模式在乐观清空时间回退后应与自由模式回收结果一致
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            fixed = self._opt(data).solve(fixed_cycle=reduce.cycle)
        self.assertAlmostEqual(fixed.cycle, reduce.cycle, places=4)
        self.assertEqual(fixed.order, reduce.order)
        self.assertAlmostEqual(fixed.waste, reduce.waste, delta=1e-3)

    # ------------------------------------------------------------------ #
    # 用户硬/软约束
    # ------------------------------------------------------------------ #
    def test_hard_user_constraint_changes_optimum(self):
        data = two_phase_data(demand_e=600, demand_w=600)
        opt = self._opt(data)
        opt.add_constraint(
            ConstraintSpec(
                "g1_min20",
                {("g", "P1"): 1.0},
                ">=",
                20.0,
                trigger=Trigger.any_of("P1"),
            )
        )
        result = opt.solve(allow_cycle_reduction=False)
        self.assertAlmostEqual(result.greens["P1"], 20.0, places=4)
        self.assertAlmostEqual(result.greens["P2"], 15.0, places=4)
        self.assertAlmostEqual(result.cycle, 45.0, places=4)
        self.assertTrue(result.verification["passed"])

    def test_soft_constraint_reports_violation(self):
        data = two_phase_data(demand_e=300, demand_w=300)
        opt = self._opt(data)
        opt.add_constraint(
            ConstraintSpec(
                "min_g1",
                {("g", "P1"): 1.0},
                ">=",
                25.0,
                soft=True,
                penalty=1000.0,
            )
        )
        result = opt.solve()
        self.assertIn("min_g1", result.sigmas)
        sigma = result.sigmas["min_g1"]
        self.assertGreater(sigma, 0.0)
        self.assertGreaterEqual(result.greens["P1"] + sigma, 25.0 - 1e-5)
        self.assertTrue(result.verification["passed"])

    def test_add_constraint_audit_blocks_impossible_hard_constraint(self):
        data = two_phase_data()
        opt = self._opt(data)
        with self.assertRaises(ConstraintAuditError):
            opt.add_constraint(ConstraintSpec("too_large", {("g", "P1"): 1.0}, ">=", 200.0))

    def test_confirm_audit_allows_registration_but_solve_infeasible(self):
        data = two_phase_data()
        opt = self._opt(data)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            opt.add_constraint(
                ConstraintSpec(
                    "too_large",
                    {("g", "P1"): 1.0},
                    ">=",
                    200.0,
                    confirm_audit=True,
                )
            )
        with self.assertRaises(InfeasibleError):
            opt.solve()

    def test_mixed_trigger_requires_confirmation(self):
        data = two_phase_data()
        opt = self._opt(data)
        with self.assertRaises(ConstraintError):
            opt.add_constraint(
                ConstraintSpec("mixed", {("g", "P1"): 1.0, ("g", "P2"): -1.0}, ">=", 0.0)
            )
        # AND 触发可编译
        opt.add_constraint(
            ConstraintSpec(
                "mixed_and",
                {("g", "P1"): 1.0, ("g", "P2"): -1.0},
                ">=",
                0.0,
                trigger=Trigger.all_of("P1", "P2"),
            )
        )
        result = opt.solve()
        self.assertTrue(result.verification["passed"])

    # ------------------------------------------------------------------ #
    # 对称性 / 极端值
    # ------------------------------------------------------------------ #
    def test_symmetry(self):
        data = two_phase_data(demand_e=600, demand_w=600)
        result = self._opt(data).solve()
        self.assertAlmostEqual(result.greens["P1"], result.greens["P2"], places=4)
        self.assertEqual(result.phase_count, 2)
        self.assertTrue(result.verification["passed"])

    def test_single_phase_extreme_saturation(self):
        data = IntersectionData(
            movements={"E": Movement("E", 1700.0)},
            phases={"P1": Phase("P1", {"E": 1800.0})},
            lost_time={},
            g_min=15.0,
            c_min=30.0,
            c_max=180.0,
        )
        result = self._opt(data).solve()
        self.assertEqual(result.phase_count, 1)
        self.assertEqual(result.selected, ["P1"])
        self.assertAlmostEqual(result.cycle, 30.0, delta=0.35)
        self.assertTrue(result.verification["passed"])
        margin = result.verification["service_margins"]["E"]
        self.assertGreaterEqual(margin, -1e-4)

    # ------------------------------------------------------------------ #
    # no-good cut 回退
    # ------------------------------------------------------------------ #
    def test_fallback_no_good_cut(self):
        data = IntersectionData(
            movements={"E": Movement("E", 300.0), "W": Movement("W", 300.0)},
            phases={
                "P1": Phase("P1", {"E": 1800.0, "W": 1800.0}),
                "P2": Phase("P2", {"E": 1800.0, "W": 1800.0}),
            },
            lost_time={("P1", "P2"): 5.0, ("P2", "P1"): 5.0},
            g_min=15.0,
            c_min=30.0,
            c_max=180.0,
        )
        original = OrderingPostProcessor.process
        state = {"calls": 0}

        def flaky_process(self, *args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                return None
            return original(self, *args, **kwargs)

        opt = self._opt(data, max_fallback=5)
        with patch.object(OrderingPostProcessor, "process", new=flaky_process):
            result = opt.solve()
        self.assertEqual(result.fallback_cuts, 1)
        self.assertEqual(result.phase_count, 1)
        self.assertTrue(result.verification["passed"])


if __name__ == "__main__":
    unittest.main()
