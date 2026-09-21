#!/usr/bin/env python3
"""
Unit Test: REQ.md Prediction vs. Result Gap Analysis (Line 16)
'The agent proposes strategies, predicts which will win, tests, and learns from the gap between prediction and result.'
"""

import unittest
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.critique_evaluator import evaluate_run


class TestPredictionGapAnalysis(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

        # Mock tool library
        self.tools_path = os.path.join(self.temp_dir.name, "tools.json")
        with open(self.tools_path, "w") as f:
            json.dump({
                "tools": [
                    {"tool_number": 1, "name": "Rougher", "diameter_mm": 10.0, "type": "endmill", "chipload_max": 0.08},
                    {"tool_number": 2, "name": "Finisher", "diameter_mm": 6.0, "type": "endmill", "chipload_max": 0.04}
                ]
            }, f)

        # Mock strategies with pre-simulation predictions
        self.strategies_path = os.path.join(self.temp_dir.name, "strategies.json")
        with open(self.strategies_path, "w") as f:
            json.dump({
                "strategies": {
                    "CYCLE_TIME": {
                        "predictions": {
                            "predicted_cycle_time_sec": 600.0,
                            "predicted_mean_deviation_um": 200.0,
                            "predicted_max_scallop_um": 600.0,
                            "predicted_max_chipload_mm": 0.08
                        }
                    },
                    "ACCURACY_TUNED": {
                        "predictions": {
                            "predicted_cycle_time_sec": 3600.0,
                            "predicted_mean_deviation_um": 10.0,
                            "predicted_max_scallop_um": 30.0,
                            "predicted_max_chipload_mm": 0.04
                        }
                    }
                }
            }, f)

        # Mock simulation results (measured kinematics)
        self.sim_path = os.path.join(self.temp_dir.name, "sim.json")
        with open(self.sim_path, "w") as f:
            json.dump({
                "CYCLE_TIME": {
                    "kinematics": {
                        "total_time_sec": 645.5,
                        "max_chipload_mm": 0.078,
                        "rapid_collisions": [],
                        "is_safe": True,
                        "tools_used": [1]
                    }
                },
                "ACCURACY_TUNED": {
                    "kinematics": {
                        "total_time_sec": 3480.0,
                        "max_chipload_mm": 0.038,
                        "rapid_collisions": [],
                        "is_safe": True,
                        "tools_used": [2]
                    }
                }
            }, f)

        # Mock deviations results (measured metrology)
        self.dev_path = os.path.join(self.temp_dir.name, "dev.json")
        with open(self.dev_path, "w") as f:
            json.dump({
                "CYCLE_TIME": {
                    "mean_deviation_um": 192.4,
                    "floor_scallop_height_um": 580.0,
                    "volumetric_fidelity_pct": 99.1,
                    "gouging_check": {"has_gouge": False}
                },
                "ACCURACY_TUNED": {
                    "mean_deviation_um": 8.5,
                    "floor_scallop_height_um": 22.0,
                    "volumetric_fidelity_pct": 99.8,
                    "gouging_check": {"has_gouge": False}
                }
            }, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prediction_gap_calculation(self):
        critique = evaluate_run(
            sim_path=self.sim_path,
            deviations_path=self.dev_path,
            tools_path=self.tools_path,
            strategies_path=self.strategies_path
        )

        self.assertIn("prediction_gaps", critique)
        gaps = critique["prediction_gaps"]
        self.assertIn("CYCLE_TIME", gaps)
        self.assertIn("ACCURACY_TUNED", gaps)

        # Check CYCLE_TIME prediction gaps
        ct_gap = gaps["CYCLE_TIME"]
        self.assertEqual(ct_gap["cycle_time"]["predicted_sec"], 600.0)
        self.assertEqual(ct_gap["cycle_time"]["measured_sec"], 645.5)
        self.assertEqual(ct_gap["cycle_time"]["delta_sec"], 45.5)
        self.assertAlmostEqual(ct_gap["cycle_time"]["error_pct"], 7.6, places=1)

        self.assertEqual(ct_gap["max_scallop"]["predicted_um"], 600.0)
        self.assertEqual(ct_gap["max_scallop"]["measured_um"], 580.0)
        self.assertEqual(ct_gap["max_scallop"]["delta_um"], -20.0)

        self.assertEqual(ct_gap["mean_deviation"]["predicted_um"], 200.0)
        self.assertEqual(ct_gap["mean_deviation"]["measured_um"], 192.4)
        self.assertEqual(ct_gap["mean_deviation"]["delta_um"], -7.6)

        self.assertEqual(ct_gap["max_chipload"]["predicted_mm"], 0.08)
        self.assertEqual(ct_gap["max_chipload"]["measured_mm"], 0.078)
        self.assertEqual(ct_gap["max_chipload"]["delta_mm"], -0.002)

        # Check ACCURACY_TUNED prediction gaps
        acc_gap = gaps["ACCURACY_TUNED"]
        self.assertEqual(acc_gap["cycle_time"]["predicted_sec"], 3600.0)
        self.assertEqual(acc_gap["cycle_time"]["measured_sec"], 3480.0)
        self.assertEqual(acc_gap["cycle_time"]["delta_sec"], -120.0)


if __name__ == "__main__":
    unittest.main()
