from __future__ import annotations

import unittest

from signal_timing import ConstraintSpec, Trigger, audit_constraint


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.g_min = 15.0
        self.c_max = 180.0

    def test_mixed_trap_example(self):
        spec = ConstraintSpec(
            "balance",
            {("g", "P1"): 1.0, ("g", "P2"): -0.8},
            ">=",
            0.0,
        )
        report = audit_constraint(spec, self.g_min, self.c_max)
        self.assertTrue(report.has_trap)
        self.assertIn(("P2",), report.trap_subsets)
        by_subset = {e.subset: e for e in report.subsets}
        self.assertEqual(by_subset[("P1",)].status, "always_satisfied")
        self.assertEqual(by_subset[("P2",)].status, "always_violated")
        self.assertEqual(by_subset[("P1", "P2")].status, "possible")
        self.assertEqual(by_subset[()].status, "always_satisfied")

    def test_and_trigger_resolves_trap(self):
        spec = ConstraintSpec(
            "balance_and",
            {("g", "P1"): 1.0, ("g", "P2"): -0.8},
            ">=",
            0.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
        report = audit_constraint(spec, self.g_min, self.c_max)
        self.assertFalse(report.has_trap)
        self.assertTrue(report.has_always_violated)  # raw 子集 {P2} 仍恒违反
        by_subset = {e.subset: e for e in report.subsets}
        self.assertTrue(by_subset[("P1", "P2")].active)
        self.assertFalse(by_subset[("P2",)].active)
        self.assertFalse(by_subset[("P2",)].is_trap)

    def test_pure_nonnegative_no_trap(self):
        spec = ConstraintSpec(
            "sum_min",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            ">=",
            20.0,
        )
        report = audit_constraint(spec, self.g_min, self.c_max)
        self.assertFalse(report.has_trap)
        # 空集下 0 >= 20 是恒违反，但由模型"至少一相"约束兜底，不计为陷阱
        by_subset = {e.subset: e for e in report.subsets}
        self.assertEqual(by_subset[()].status, "always_violated")
        self.assertFalse(by_subset[()].is_trap)
        self.assertEqual(by_subset[("P1",)].status, "possible")

    def test_equality_trap_when_partial_selection(self):
        spec = ConstraintSpec(
            "eq",
            {("g", "P1"): 1.0, ("g", "P2"): -1.0},
            "==",
            0.0,
        )
        report = audit_constraint(spec, self.g_min, self.c_max)
        # 只选 P1 时 g1>=15, g2=0，等式 0 不可能
        self.assertTrue(report.has_trap)

    def test_soft_constraint_not_trap(self):
        spec = ConstraintSpec(
            "soft_min",
            {("g", "P1"): 1.0},
            ">=",
            25.0,
            soft=True,
            penalty=1000.0,
        )
        report = audit_constraint(spec, self.g_min, self.c_max)
        self.assertFalse(report.has_trap)
        self.assertTrue(report.has_always_violated)  # 空集下 0 >= 25 恒违反，但软约束不视为陷阱
        self.assertIn("软约束", report.note)

    def test_non_g_variable_manual_review(self):
        spec = ConstraintSpec(
            "with_C",
            {("g", "P1"): 1.0, ("C", ""): -0.5},
            ">=",
            50.0,
        )
        report = audit_constraint(spec, self.g_min, self.c_max)
        self.assertTrue(report.needs_manual_review)
        self.assertIn("非 g", report.note)

    def test_audit_table_contains_labels(self):
        spec = ConstraintSpec(
            "balance",
            {("g", "P1"): 1.0, ("g", "P2"): -0.8},
            ">=",
            0.0,
        )
        table = audit_constraint(spec, self.g_min, self.c_max).as_table()
        self.assertIn("恒违反", table)
        self.assertIn("balance", table)


if __name__ == "__main__":
    unittest.main()
