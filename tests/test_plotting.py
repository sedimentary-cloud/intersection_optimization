from __future__ import annotations

import os
import tempfile
import unittest

from signal_timing import IntersectionData, LexicographicOptimizer, Movement, Phase
from signal_timing.plotting import (
    ScheduleInterval,
    SchedulePlan,
    build_schedule,
    compute_satisfaction_times,
    plot_movement_release_gantt,
    plot_signal_timing_gantt,
    satisfaction_time,
)


class TestPlotting(unittest.TestCase):
    def setUp(self):
        self.data = IntersectionData(
            movements={"A": Movement("A", 300.0), "B": Movement("B", 300.0)},
            phases={
                "P1": Phase("P1", {"A": 1800.0}),
                "P2": Phase("P2", {"B": 1800.0}),
            },
            lost_time={("P1", "P2"): 2.0, ("P2", "P1"): 20.0},
            g_min=15.0,
            c_min=30.0,
            c_max=180.0,
        )
        self.result = LexicographicOptimizer(self.data, mip_rel_gap=0.001).solve()

    def test_build_schedule_intervals(self):
        plan = build_schedule(self.data, self.result)
        self.assertEqual(plan.order, self.result.order)
        self.assertAlmostEqual(plan.cycle, self.result.cycle)
        greens = [iv for iv in plan.intervals if iv.kind == "green"]
        self.assertEqual({iv.phase for iv in greens}, set(plan.order))
        self.assertEqual(len(greens), len(plan.order))
        # 两个相位时，真实清空时间之和 = 2 + 20 = 22
        self.assertAlmostEqual(plan.clearance_total, 22.0)
        # 区间不重叠且不超过周期
        for iv in plan.intervals:
            self.assertGreaterEqual(iv.duration, 0.0)
            self.assertLessEqual(iv.end, plan.cycle + 1e-6)

    def test_enforce_zero_slack_fixed_cycle(self):
        result = LexicographicOptimizer(self.data, mip_rel_gap=0.001).solve(
            fixed_cycle=180.0,
            enforce_zero_slack=True,
        )
        plan = build_schedule(self.data, result)
        total_green = sum(result.greens[p] for p in result.selected)
        self.assertAlmostEqual(result.cycle, 180.0)
        self.assertAlmostEqual(
            total_green + plan.clearance_total, 180.0, places=4
        )
        slack = [iv for iv in plan.intervals if iv.kind == "slack"]
        self.assertFalse(slack)

    def test_fixed_cycle_has_slack_interval(self):
        result = LexicographicOptimizer(self.data, mip_rel_gap=0.001).solve(
            fixed_cycle=180.0
        )
        self.assertAlmostEqual(result.cycle, 180.0)
        plan = build_schedule(self.data, result)
        self.assertAlmostEqual(plan.cycle, 180.0)
        slack = [iv for iv in plan.intervals if iv.kind == "slack"]
        self.assertTrue(slack)
        self.assertGreater(slack[0].duration, 0.0)
        self.assertLessEqual(
            max(iv.end for iv in plan.intervals), plan.cycle + 1e-6
        )

    def test_satisfaction_time_manual_plan(self):
        data = IntersectionData(
            movements={"E": Movement("E", 600.0)},
            phases={"P1": Phase("P1", {"E": 1800.0})},
            lost_time={},
            g_min=11.0,
            c_min=30.0,
            c_max=180.0,
        )
        plan = SchedulePlan(
            order=["P1"],
            greens={"P1": 15.0},
            cycle=30.0,
            intervals=[ScheduleInterval("green", "P1", 0.0, 15.0, phase="P1")],
            clearance_total=0.0,
        )
        # 周期需求 = 600*30/3600 = 5 veh；服务率 = 1800/3600 = 0.5 veh/s
        self.assertAlmostEqual(satisfaction_time(data, plan, "E"), 10.0)

    def test_satisfaction_times_on_solved_result(self):
        times = compute_satisfaction_times(self.data, self.result)
        self.assertEqual(set(times), set(self.data.movements))
        for mid, t_sat in times.items():
            self.assertIsNotNone(t_sat, msg=f"{mid} 未满足")
            self.assertGreaterEqual(t_sat, -1e-9)
            self.assertLessEqual(t_sat, self.result.cycle + 1e-9)

    def test_movement_release_gantt_saves_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "movement_gantt.png")
            # 默认只画一个周期
            fig = plot_movement_release_gantt(
                self.data, self.result, save_path=out, show=False
            )
            self.assertEqual(len(fig.axes), 1)
            self.assertAlmostEqual(fig.axes[0].get_xlim()[1], self.result.cycle)
            self.assertTrue(os.path.exists(out))
            self.assertGreater(os.path.getsize(out), 10_000)

    def test_plot_saves_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "gantt.png")
            fig = plot_signal_timing_gantt(
                self.data, self.result, cycles=2, save_path=out, show=False
            )
            self.assertTrue(os.path.exists(out))
            self.assertGreater(os.path.getsize(out), 10_000)
            self.assertIsNotNone(fig)


if __name__ == "__main__":
    unittest.main()
