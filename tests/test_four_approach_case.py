from __future__ import annotations

import unittest

from signal_timing import LexicographicOptimizer
from tests.helpers import four_approach_data


class TestFourApproachOverlapCase(unittest.TestCase):
    def _solve(self, include_overlap: bool):
        data = four_approach_data(include_overlap=include_overlap)
        result = LexicographicOptimizer(
            data, mip_rel_gap=0.001, time_limit=30.0
        ).solve(allow_cycle_reduction=True)
        return data, result

    def test_overlap_case_feasible_and_min_green(self):
        data, result = self._solve(include_overlap=True)
        self.assertTrue(result.verification["passed"])
        self.assertTrue(result.selected)
        self.assertTrue(set(result.selected) <= set(data.phase_ids))
        # 所有被选相位绿灯严格大于 10s
        for pid in result.selected:
            self.assertGreater(result.greens[pid], 10.0)
            self.assertGreaterEqual(result.greens[pid], data.g_min - 1e-6)
        # 每个流向在周期末都被满足
        for mid in data.movements:
            demand = data.movements[mid].demand * result.cycle / 3600.0
            provided = (
                sum(
                    data.phases[p].capacity.get(mid, 0.0) * result.greens[p]
                    for p in result.order
                )
                / 3600.0
            )
            self.assertGreaterEqual(provided - demand, -1e-5)
        self.assertLessEqual(result.cycle, data.c_max)
        # §9.5 周期回收：最终周期应不大于第一阶段保守周期
        self.assertLessEqual(result.cycle, result.stage1_cycle + 1e-6)

    def test_symmetric_control_feasible(self):
        data, result = self._solve(include_overlap=False)
        self.assertTrue(result.verification["passed"])
        self.assertTrue(result.selected)
        # 没有搭接相位可选时，只能从 4 个对称相位中选择
        self.assertTrue(set(result.selected) <= {"P1_NS_TH", "P2_NS_LT", "P3_EW_TH", "P4_EW_LT"})
        for pid in result.selected:
            self.assertGreaterEqual(result.greens[pid], data.g_min - 1e-6)

    def test_overlap_does_not_increase_cycle(self):
        _, overlap = self._solve(include_overlap=True)
        _, symmetric = self._solve(include_overlap=False)
        self.assertLessEqual(overlap.cycle, symmetric.cycle + 1e-6)


if __name__ == "__main__":
    unittest.main()
