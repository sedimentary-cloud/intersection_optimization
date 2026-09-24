from __future__ import annotations

import unittest

from signal_timing import (
    ConstraintCompiler,
    ConstraintError,
    ConstraintSpec,
    Trigger,
)
from tests.helpers import compiler_registry


class TestConstraintCompiler(unittest.TestCase):
    def setUp(self):
        self.reg = compiler_registry(("P1", "P2"))
        self.compiler = ConstraintCompiler(self.reg.get_bound, 180.0)

    # ------------------------------------------------------------------ #
    # Trigger / spec
    # ------------------------------------------------------------------ #
    def test_trigger_semantics(self):
        always = Trigger.always()
        any_of = Trigger.any_of("P1", "P2")
        all_of = Trigger.all_of("P1", "P2", "P1")
        self.assertTrue(always.is_active(set()))
        self.assertTrue(any_of.is_active({"P2"}))
        self.assertFalse(any_of.is_active(set()))
        self.assertTrue(all_of.is_active({"P1", "P2"}))
        self.assertFalse(all_of.is_active({"P1"}))
        self.assertEqual(all_of.phases, ("P1", "P2"))

    def test_constraint_spec_validation(self):
        with self.assertRaises(ConstraintError):
            ConstraintSpec("bad_sense", {("g", "P1"): 1.0}, "<", 0.0)
        with self.assertRaises(ConstraintError):
            ConstraintSpec("soft_no_penalty", {("g", "P1"): 1.0}, ">=", 0.0, soft=True)
        with self.assertRaises(ConstraintError):
            ConstraintSpec("empty", {}, ">=", 0.0)
        with self.assertRaises(ConstraintError):
            ConstraintSpec("bad_key", {"gP1": 1.0}, ">=", 0.0)
        with self.assertRaises(ConstraintError):
            Trigger.any_of()

    # ------------------------------------------------------------------ #
    # big-M
    # ------------------------------------------------------------------ #
    def test_big_m_formula(self):
        spec_le = ConstraintSpec(
            "m_le",
            {("g", "P1"): 1.0, ("g", "P2"): -0.8, ("C", ""): 0.5},
            "<=",
            10.0,
            confirm_mixed_trigger=True,
        )
        # M+ = 1*180 + (-0.8)*0 + 0.5*180 - 10 = 260
        self.assertAlmostEqual(self.compiler.raw_big_m(spec_le, "<="), 260.0)
        self.assertAlmostEqual(self.compiler.big_m(spec_le, "<="), 1.05 * 260.0 + 1e-6)

        spec_ge = ConstraintSpec(
            "m_ge",
            {("g", "P1"): 1.0, ("g", "P2"): -0.8, ("C", ""): 0.5},
            ">=",
            10.0,
            confirm_mixed_trigger=True,
        )
        # M- = 10 - (1*0 + (-0.8)*180 + 0.5*0) = 154
        self.assertAlmostEqual(self.compiler.raw_big_m(spec_ge, ">="), 154.0)
        self.assertAlmostEqual(self.compiler.big_m(spec_ge, ">="), 1.05 * 154.0 + 1e-6)

    # ------------------------------------------------------------------ #
    # 发射规则矩阵
    # ------------------------------------------------------------------ #
    def test_always_direct(self):
        spec = ConstraintSpec("always_le", {("g", "P1"): 1.0, ("g", "P2"): 1.0}, "<=", 10.0)
        rows, slacks = self.compiler.compile(spec)
        self.assertEqual(len(rows), 1)
        self.assertEqual(slacks, [])
        self.assertEqual(rows[0].coeffs, dict(spec.coeffs))
        self.assertEqual(rows[0].rhs, 10.0)
        self.assertEqual(rows[0].big_m, 0.0)

    def test_and_le(self):
        spec = ConstraintSpec(
            "and_le",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            "<=",
            10.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 1)
        M = 1.05 * 350.0 + 1e-6  # raw M+ = 180+180-10
        row = rows[0]
        self.assertAlmostEqual(row.coeffs[("y", "P1")], M)
        self.assertAlmostEqual(row.coeffs[("y", "P2")], M)
        self.assertAlmostEqual(row.rhs, 10.0 + 2.0 * M)
        self.assertAlmostEqual(row.big_m, M)

    def test_and_ge(self):
        spec = ConstraintSpec(
            "and_ge",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            ">=",
            100.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        M = 1.05 * 100.0 + 1e-6  # raw M- = 100 - 0
        row = rows[0]
        self.assertAlmostEqual(row.coeffs[("y", "P1")], -M)
        self.assertAlmostEqual(row.coeffs[("y", "P2")], -M)
        self.assertAlmostEqual(row.rhs, 100.0 - 2.0 * M)

    def test_or_le_nonnegative_rhs_direct(self):
        spec = ConstraintSpec(
            "or_le_pos",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            "<=",
            10.0,
            trigger=Trigger.any_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].coeffs, dict(spec.coeffs))
        self.assertEqual(rows[0].big_m, 0.0)

    def test_or_le_negative_rhs_copies(self):
        spec = ConstraintSpec(
            "or_le_neg",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            "<=",
            -5.0,
            trigger=Trigger.any_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 2)
        M = 1.05 * (180.0 + 180.0 - (-5.0)) + 1e-6
        for row in rows:
            self.assertEqual(row.sense, "<=")
            self.assertAlmostEqual(row.rhs, -5.0 + M)
            self.assertAlmostEqual(row.big_m, M)
            ykeys = [k for k in row.coeffs if k[0] == "y"]
            self.assertEqual(len(ykeys), 1)
            self.assertAlmostEqual(row.coeffs[ykeys[0]], M)

    def test_or_ge_copies(self):
        spec = ConstraintSpec(
            "or_ge",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            ">=",
            100.0,
            trigger=Trigger.any_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 2)
        M = 1.05 * 100.0 + 1e-6
        for row in rows:
            self.assertEqual(row.sense, ">=")
            self.assertAlmostEqual(row.rhs, 100.0 - M)
            ykeys = [k for k in row.coeffs if k[0] == "y"]
            self.assertEqual(len(ykeys), 1)
            self.assertAlmostEqual(row.coeffs[ykeys[0]], -M)

    def test_equality_split(self):
        spec = ConstraintSpec(
            "eq",
            {("g", "P1"): 1.0, ("g", "P2"): -1.0},
            "==",
            0.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 2)
        senses = {r.sense for r in rows}
        self.assertEqual(senses, {"<=", ">="})
        self.assertTrue(any("[<=]" in r.name for r in rows))
        self.assertTrue(any("[>=]" in r.name for r in rows))

    def test_m_le_zero_direct(self):
        spec = ConstraintSpec(
            "redundant",
            {("g", "P1"): 1.0},
            ">=",
            -10.0,
            trigger=Trigger.any_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].coeffs, {("g", "P1"): 1.0})
        self.assertEqual(rows[0].big_m, 0.0)

    # ------------------------------------------------------------------ #
    # 软约束
    # ------------------------------------------------------------------ #
    def test_soft_slack(self):
        spec = ConstraintSpec(
            "soft_min",
            {("g", "P1"): 1.0},
            ">=",
            100.0,
            soft=True,
            penalty=500.0,
        )
        rows, slacks = self.compiler.compile(spec)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(slacks), 1)
        sigma = slacks[0]
        self.assertEqual(sigma.key, ("sigma", "soft_min:-"))
        self.assertEqual(sigma.penalty, 500.0)
        self.assertEqual(sigma.ub, 180.0)
        self.assertEqual(rows[0].coeffs[sigma.key], 1.0)

    def test_soft_equality_two_slacks(self):
        spec = ConstraintSpec(
            "soft_eq",
            {("g", "P1"): 1.0, ("g", "P2"): -1.0},
            "==",
            0.0,
            trigger=Trigger.all_of("P1", "P2"),
            soft=True,
            penalty=500.0,
        )
        rows, slacks = self.compiler.compile(spec)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(slacks), 2)
        self.assertEqual({s.sign for s in slacks}, {"+", "-"})

    # ------------------------------------------------------------------ #
    # 触发坍缩
    # ------------------------------------------------------------------ #
    def test_trigger_collapse_active(self):
        spec = ConstraintSpec(
            "and_collapse",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            ">=",
            100.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
        rows, _ = self.compiler.compile(
            spec, fixed_values={("y", "P1"): 1.0, ("y", "P2"): 1.0}
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].coeffs, {("g", "P1"): 1.0, ("g", "P2"): 1.0})
        self.assertEqual(rows[0].rhs, 100.0)
        self.assertEqual(rows[0].big_m, 0.0)

    def test_trigger_collapse_inactive(self):
        spec = ConstraintSpec(
            "and_collapse",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            ">=",
            100.0,
            trigger=Trigger.all_of("P1", "P2"),
        )
        rows, slacks = self.compiler.compile(
            spec, fixed_values={("y", "P1"): 1.0, ("y", "P2"): 0.0}
        )
        self.assertEqual(rows, [])
        self.assertEqual(slacks, [])

    # ------------------------------------------------------------------ #
    # 混合系数保护 / OR 存在性
    # ------------------------------------------------------------------ #
    def test_mixed_trigger_guard(self):
        spec = ConstraintSpec(
            "mixed_always",
            {("g", "P1"): 1.0, ("g", "P2"): -1.0},
            ">=",
            0.0,
        )
        with self.assertRaises(ConstraintError):
            self.compiler.compile(spec)
        spec_ok = ConstraintSpec(
            "mixed_confirmed",
            {("g", "P1"): 1.0, ("g", "P2"): -1.0},
            ">=",
            0.0,
            confirm_mixed_trigger=True,
        )
        with self.assertWarns(RuntimeWarning):
            rows, _ = self.compiler.compile(spec_ok)
        self.assertEqual(len(rows), 1)

    def test_or_existential(self):
        spec = ConstraintSpec(
            "or_exist",
            {("g", "P1"): 1.0, ("g", "P2"): 1.0},
            ">=",
            100.0,
            trigger=Trigger.any_of("P1", "P2"),
            or_existential=True,
        )
        rows, _ = self.compiler.compile(spec)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].coeffs, dict(spec.coeffs))
        self.assertEqual(rows[0].big_m, 0.0)


if __name__ == "__main__":
    unittest.main()
