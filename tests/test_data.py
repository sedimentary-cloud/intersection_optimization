from __future__ import annotations

import unittest

from signal_timing import DataValidationError, IntersectionData, Movement, Phase
from tests.helpers import two_phase_data


class TestDataLayer(unittest.TestCase):
    def test_valid_data_derived_quantities(self):
        data = two_phase_data()
        self.assertEqual(data.l_bar, 5.0)
        self.assertEqual(data.l_min, 5.0)
        self.assertEqual(data.movement_ids, ["E", "W"])
        self.assertEqual(data.phase_ids, ["P1", "P2"])
        self.assertEqual(data.max_capacity("E"), 1800.0)
        self.assertEqual(data.service_phase_ids("W"), ["P2"])
        self.assertEqual(data.clearance("P1", "P2"), 5.0)
        self.assertEqual(data.clearance("P1", "P1"), 0.0)
        self.assertAlmostEqual(data.order_clearance(["P1", "P2"]), 10.0)
        self.assertAlmostEqual(data.order_clearance(["P1"]), 0.0)

    def test_uncovered_movement_raises(self):
        with self.assertRaises(DataValidationError) as ctx:
            IntersectionData(
                movements={"E": Movement("E", 100), "W": Movement("W", 100)},
                phases={"P1": Phase("P1", {"E": 1000})},
                lost_time={},
                g_min=15,
                c_min=30,
                c_max=180,
            )
        self.assertIn("W", str(ctx.exception))

    def test_nonpositive_demand_raises(self):
        with self.assertRaises(DataValidationError):
            Movement("E", 0.0)

    def test_nonpositive_capacity_raises(self):
        with self.assertRaises(DataValidationError):
            IntersectionData(
                movements={"E": Movement("E", 100)},
                phases={"P1": Phase("P1", {"E": 0.0})},
                lost_time={},
                g_min=15,
                c_min=30,
                c_max=180,
            )

    def test_unknown_movement_in_capacity_raises(self):
        with self.assertRaises(DataValidationError):
            IntersectionData(
                movements={"E": Movement("E", 100)},
                phases={"P1": Phase("P1", {"X": 1000})},
                lost_time={},
                g_min=15,
                c_min=30,
                c_max=180,
            )

    def test_unknown_phase_in_lost_time_raises(self):
        with self.assertRaises(DataValidationError):
            IntersectionData(
                movements={"E": Movement("E", 100), "W": Movement("W", 100)},
                phases={"P1": Phase("P1", {"E": 1000}), "P2": Phase("P2", {"W": 1000})},
                lost_time={("P1", "PX"): 5.0},
                g_min=15,
                c_min=30,
                c_max=180,
            )

    def test_time_bounds_validation(self):
        with self.assertRaises(DataValidationError):
            two_phase_data(g_min=30, c_min=30, c_max=180)
        with self.assertRaises(DataValidationError):
            two_phase_data(g_min=15, c_min=200, c_max=180)

    def test_static_precheck_ratio_ge_one(self):
        with self.assertRaises(DataValidationError) as ctx:
            two_phase_data(demand_e=2000, capacity_e=1800)
        self.assertIn("E", str(ctx.exception))

    def test_static_precheck_required_cycle(self):
        # ratio=0.9, l_bar=50 -> required C = 50/(0.1)=500 > c_max
        with self.assertRaises(DataValidationError):
            IntersectionData(
                movements={"E": Movement("E", 900), "W": Movement("W", 100)},
                phases={"P1": Phase("P1", {"E": 1000}), "P2": Phase("P2", {"W": 1000})},
                lost_time={("P1", "P2"): 50.0, ("P2", "P1"): 50.0},
                g_min=15,
                c_min=30,
                c_max=180,
            )

    def test_clearance_strict_mode(self):
        data2 = IntersectionData(
            movements={"E": Movement("E", 100), "W": Movement("W", 100)},
            phases={"P1": Phase("P1", {"E": 1000}), "P2": Phase("P2", {"W": 1000})},
            lost_time={("P1", "P2"): 5.0},
            g_min=15,
            c_min=30,
            c_max=180,
            strict_clearance=True,
        )
        with self.assertRaises(DataValidationError):
            data2.clearance("P2", "P1")
        data3 = IntersectionData(
            movements={"E": Movement("E", 100), "W": Movement("W", 100)},
            phases={"P1": Phase("P1", {"E": 1000}), "P2": Phase("P2", {"W": 1000})},
            lost_time={("P1", "P2"): 5.0},
            g_min=15,
            c_min=30,
            c_max=180,
            strict_clearance=False,
        )
        self.assertEqual(data3.clearance("P2", "P1"), 0.0)

    def test_empty_lost_time_l_bar_zero(self):
        data = IntersectionData(
            movements={"E": Movement("E", 100), "W": Movement("W", 100)},
            phases={"P1": Phase("P1", {"E": 1000}), "P2": Phase("P2", {"W": 1000})},
            lost_time={},
            g_min=15,
            c_min=30,
            c_max=180,
        )
        self.assertEqual(data.l_bar, 0.0)


if __name__ == "__main__":
    unittest.main()
