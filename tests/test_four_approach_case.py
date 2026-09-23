from __future__ import annotations

import unittest

from signal_timing import (
    ConstraintSpec,
    DataValidationError,
    LexicographicOptimizer,
    Trigger,
    make_reference_order_filter,
)
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

    def test_sequence_order_constraint(self):
        def sequence_filter(order):
            order = list(order)
            if order[0] != "P1_NS_TH":
                return False
            if set(order[-2:]) != {"P2_NS_LT", "P4_EW_LT"}:
                return False
            pos = {p: i for i, p in enumerate(order)}
            overlaps = [p for p in ("P5_N_THLT", "P6_S_THLT") if p in pos]
            if not overlaps:
                return False
            if "P3_EW_TH" in pos:
                for p in overlaps:
                    if not (pos[p] < pos["P3_EW_TH"]):
                        return False
            return True

        data = four_approach_data(include_overlap=True)
        opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0)
        # 强制选中 P2_NS_LT，使"两个左转"都出现在顺序中
        opt.add_constraint(
            ConstraintSpec(
                name="force_P2_NS_LT",
                coeffs={("y", "P2_NS_LT"): 1.0},
                sense=">=",
                rhs=1.0,
            )
        )
        result = opt.solve(order_filter=sequence_filter, allow_cycle_reduction=True)
        self.assertTrue(result.verification["passed"])
        self.assertEqual(result.order[0], "P1_NS_TH")
        self.assertEqual(set(result.order[-2:]), {"P2_NS_LT", "P4_EW_LT"})
        self.assertIn("P2_NS_LT", result.selected)
        for pid in result.selected:
            self.assertGreaterEqual(result.greens[pid], data.g_min - 1e-6)

    def test_reference_order_filter_and_data_validation(self):
        # 数据层要求 reference_order 覆盖全部候选相位
        with self.assertRaises(DataValidationError):
            four_approach_data(
                include_overlap=True,
                reference_order=["P1_NS_TH", "P5_N_THLT"],
            )
        ref = [
            "P1_NS_TH", "P5_N_THLT", "P6_S_THLT",
            "P3_EW_TH", "P2_NS_LT", "P4_EW_LT",
        ]
        flt = make_reference_order_filter(ref)
        self.assertTrue(flt(("P1_NS_TH", "P5_N_THLT", "P3_EW_TH")))
        self.assertFalse(flt(("P1_NS_TH", "P3_EW_TH", "P5_N_THLT")))
        self.assertFalse(flt(("P1_NS_TH", "P6_S_THLT", "P5_N_THLT")))

    def test_reference_order_hard_mode(self):
        ref = [
            "P1_NS_TH", "P5_N_THLT", "P6_S_THLT",
            "P3_EW_TH", "P2_NS_LT", "P4_EW_LT",
        ]
        data = four_approach_data(include_overlap=True)
        result = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0).solve(
            reference_order=ref,
            reference_mode="hard",
            allow_cycle_reduction=True,
        )
        # 未强制 P2 时选中集合不含 P2，参考顺序诱导出：
        # P1 -> P5 -> P6 -> P3 -> P4
        self.assertEqual(
            result.order,
            ["P1_NS_TH", "P5_N_THLT", "P6_S_THLT", "P3_EW_TH", "P4_EW_LT"],
        )
        self.assertTrue(result.verification["passed"])

    def test_strict_green_and_p5_p2_sum_rule(self):
        ref_groups = [
            ("P1_NS_TH",),
            ("P5_N_THLT", "P6_S_THLT"),
            ("P2_NS_LT",),
            ("P3_EW_TH",),
            ("P7_E_THLT", "P8_W_THLT"),
            ("P4_EW_LT",),
        ]
        data = four_approach_data(
            include_overlap=True,
            include_ew_overlap=True,
            reference_order=ref_groups,
        )
        data.g_min = 15.0
        opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0)
        opt.add_constraint(
            ConstraintSpec(
                name="force_P2_NS_LT",
                coeffs={("y", "P2_NS_LT"): 1.0},
                sense=">=",
                rhs=1.0,
            )
        )
        opt.add_constraint(
            ConstraintSpec(
                name="force_P5_N_THLT",
                coeffs={("y", "P5_N_THLT"): 1.0},
                sense=">=",
                rhs=1.0,
            )
        )
        opt.add_constraint(
            ConstraintSpec(
                name="P5_plus_P2_min_32",
                coeffs={("g", "P5_N_THLT"): 1.0, ("g", "P2_NS_LT"): 1.0},
                sense=">=",
                rhs=32.0,
                trigger=Trigger.all_of("P5_N_THLT", "P2_NS_LT"),
            )
        )
        # 非齐次约束：打破 g_min 统一提高导致的等比缩放
        opt.add_constraint(
            ConstraintSpec(
                name="P5_minus_P2_ge_5",
                coeffs={("g", "P5_N_THLT"): 1.0, ("g", "P2_NS_LT"): -1.0},
                sense=">=",
                rhs=5.0,
                trigger=Trigger.all_of("P5_N_THLT", "P2_NS_LT"),
            )
        )
        result = opt.solve(reference_mode="hard", allow_cycle_reduction=True)
        self.assertTrue(result.verification["passed"])
        for pid in result.selected:
            self.assertGreaterEqual(result.greens[pid], 15.0 - 1e-6)
        self.assertGreaterEqual(
            result.greens["P5_N_THLT"] + result.greens["P2_NS_LT"],
            32.0 - 1e-6,
        )
        self.assertGreaterEqual(
            result.greens["P5_N_THLT"] - result.greens["P2_NS_LT"],
            5.0 - 1e-6,
        )

    def test_ns_then_ew_reference_order(self):
        ref_groups = [
            ("P1_NS_TH",),                  # 南北直行
            ("P5_N_THLT", "P6_S_THLT"),     # 南北搭接
            ("P2_NS_LT",),                  # 南北左转
            ("P3_EW_TH",),                  # 东西直行
            ("P7_E_THLT", "P8_W_THLT"),     # 东西搭接
            ("P4_EW_LT",),                  # 东西左转
        ]
        data = four_approach_data(
            include_overlap=True,
            include_ew_overlap=True,
            reference_order=ref_groups,
        )
        opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0)
        opt.add_constraint(
            ConstraintSpec(
                name="force_P2_NS_LT",
                coeffs={("y", "P2_NS_LT"): 1.0},
                sense=">=",
                rhs=1.0,
            )
        )
        result = opt.solve(reference_mode="hard", allow_cycle_reduction=True)
        # 南北组：P1 -> P5 -> P2；东西组：P3 -> P4（东西搭接 P7/P8 未选中）
        self.assertEqual(
            result.order,
            ["P1_NS_TH", "P5_N_THLT", "P2_NS_LT", "P3_EW_TH", "P4_EW_LT"],
        )
        self.assertTrue(result.verification["passed"])

    def test_grouped_reference_order_allows_ties(self):
        ref_groups = [
            ("P1_NS_TH",),
            ("P5_N_THLT", "P6_S_THLT"),
            ("P3_EW_TH",),
            ("P2_NS_LT", "P4_EW_LT"),
        ]
        data = four_approach_data(include_overlap=True, reference_order=ref_groups)
        opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0)
        opt.add_constraint(
            ConstraintSpec(
                name="force_P2_NS_LT",
                coeffs={("y", "P2_NS_LT"): 1.0},
                sense=">=",
                rhs=1.0,
            )
        )
        result = opt.solve(reference_mode="hard", allow_cycle_reduction=True)
        # 第 4 层内 P2/P4 可互换；硬模式枚举层内排列后选择浪费更小的顺序
        self.assertEqual(
            result.order,
            ["P1_NS_TH", "P5_N_THLT", "P3_EW_TH", "P4_EW_LT", "P2_NS_LT"],
        )
        self.assertEqual(set(result.order[-2:]), {"P2_NS_LT", "P4_EW_LT"})
        self.assertAlmostEqual(result.cycle, 115.541, places=2)
        self.assertTrue(result.verification["passed"])

    def test_reference_order_with_forced_p2(self):
        ref = [
            "P1_NS_TH", "P5_N_THLT", "P6_S_THLT",
            "P3_EW_TH", "P2_NS_LT", "P4_EW_LT",
        ]
        data = four_approach_data(include_overlap=True, reference_order=ref)
        opt = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0)
        opt.add_constraint(
            ConstraintSpec(
                name="force_P2_NS_LT",
                coeffs={("y", "P2_NS_LT"): 1.0},
                sense=">=",
                rhs=1.0,
            )
        )
        # 使用 data.reference_order，硬模式
        result = opt.solve(reference_mode="hard", allow_cycle_reduction=True)
        self.assertEqual(result.order[0], "P1_NS_TH")
        self.assertEqual(set(result.order[-2:]), {"P2_NS_LT", "P4_EW_LT"})
        self.assertTrue(result.verification["passed"])

    def test_reference_order_prefer_falls_back(self):
        # 构造一个 hard order 不可行、但全枚举可行的参考顺序：
        # 参考顺序把 P6 放在 P5 前面；当前选中集合 P1,P3,P4,P5,P6，
        # hard order = P1 -> P6 -> P5 -> P3 -> P4，通常仍可行；
        # 这里只验证 prefer 模式能返回一个合法顺序。
        ref = [
            "P1_NS_TH", "P6_S_THLT", "P5_N_THLT",
            "P3_EW_TH", "P2_NS_LT", "P4_EW_LT",
        ]
        data = four_approach_data(include_overlap=True)
        result = LexicographicOptimizer(data, mip_rel_gap=0.001, time_limit=30.0).solve(
            reference_order=ref,
            reference_mode="prefer",
            allow_cycle_reduction=True,
        )
        self.assertIsNotNone(result.order)
        self.assertTrue(result.verification["passed"])

    def test_overlap_does_not_increase_cycle(self):
        _, overlap = self._solve(include_overlap=True)
        _, symmetric = self._solve(include_overlap=False)
        self.assertLessEqual(overlap.cycle, symmetric.cycle + 1e-6)


if __name__ == "__main__":
    unittest.main()
