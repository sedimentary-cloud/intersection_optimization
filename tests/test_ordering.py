from __future__ import annotations

import unittest

from signal_timing import (
    ConstraintSpec,
    DataValidationError,
    IntersectionData,
    Movement,
    OrderingPostProcessor,
    Phase,
    Trigger,
)
from tests.helpers import three_phase_asym_data, two_phase_data


class TestOrderingPostProcessor(unittest.TestCase):
    def test_tight_cycle_chooses_low_clearance_order(self):
        data = three_phase_asym_data(low=2.0, high=20.0)
        proc = OrderingPostProcessor(data)
        result = proc.process(["P1", "P2", "P3"], 51.0, cycle_fixed=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.order, ["P1", "P2", "P3"])
        self.assertAlmostEqual(result.cycle, 51.0)
        for g in result.greens.values():
            self.assertAlmostEqual(g, 15.0)

    def test_order_filter(self):
        data = three_phase_asym_data(low=2.0, high=20.0)
        proc = OrderingPostProcessor(data)

        def reject_low(order):
            return tuple(order) != ("P1", "P2", "P3")

        result = proc.process(
            ["P1", "P2", "P3"], 120.0, cycle_fixed=True, order_filter=reject_low
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.order, ["P1", "P3", "P2"])
        for g in result.greens.values():
            self.assertAlmostEqual(g, 20.0)

    def test_all_orders_infeasible_returns_none(self):
        data = three_phase_asym_data(low=2.0, high=20.0)
        proc = OrderingPostProcessor(data)
        result = proc.process(["P1", "P2", "P3"], 50.0, cycle_fixed=True)
        self.assertIsNone(result)

    def test_single_phase(self):
        data = IntersectionData(
            movements={"E": Movement("E", 600.0)},
            phases={"P1": Phase("P1", {"E": 1800.0})},
            lost_time={},
            g_min=15.0,
            c_min=30.0,
            c_max=180.0,
        )
        proc = OrderingPostProcessor(data)
        result = proc.process(["P1"], 30.0, cycle_fixed=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.order, ["P1"])
        self.assertAlmostEqual(result.greens["P1"], 15.0)
        self.assertAlmostEqual(result.cycle, 30.0)
        self.assertAlmostEqual(result.waste, 1800.0 * 15.0 - 600.0 * 30.0)

    def test_missing_clearance_raises(self):
        data = IntersectionData(
            movements={"E": Movement("E", 100), "W": Movement("W", 100)},
            phases={"P1": Phase("P1", {"E": 1000}), "P2": Phase("P2", {"W": 1000})},
            lost_time={("P1", "P2"): 5.0},
            g_min=15,
            c_min=30,
            c_max=180,
            strict_clearance=True,
        )
        proc = OrderingPostProcessor(data)
        with self.assertRaises(DataValidationError):
            proc.process(["P1", "P2"], 60.0, cycle_fixed=True)

    def test_soft_constraint_in_ordering(self):
        data = two_phase_data()
        spec = ConstraintSpec(
            "min_g1",
            {("g", "P1"): 1.0},
            ">=",
            25.0,
            soft=True,
            penalty=1000.0,
        )
        proc = OrderingPostProcessor(data, [spec])
        result = proc.process(["P1", "P2"], 60.0, cycle_fixed=True)
        self.assertIsNotNone(result)
        self.assertIn("min_g1", result.sigmas)
        self.assertGreaterEqual(result.sigmas["min_g1"], 0.0)
        # row: g1 + sigma >= 25
        self.assertGreaterEqual(result.greens["P1"] + result.sigmas["min_g1"], 25.0 - 1e-6)


if __name__ == "__main__":
    unittest.main()
