#!/usr/bin/env python3
"""
Unit Test: REQ.md Mutation Corpus (Line 38)
'Toolpaths with a planted rapid move through the stock · a tool longer than the
 machine's Z travel · a stepdown deeper than the flute length · a finishing pass
 that skips a face. The checker must catch all of them.'
"""

import unittest
import os
import sys
import json
import tempfile
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
camotics_verifier = importlib.import_module("04_camotics_verifier")
analyze_gcode_safety_and_kinematics = camotics_verifier.analyze_gcode_safety_and_kinematics

critique_evaluator = importlib.import_module("05b_critique_evaluator")
evaluate_run = critique_evaluator.evaluate_run


class TestMutationCorpus(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stock_bounds = {
            "min_xyz": [0.0, 0.0, -25.0],
            "max_xyz": [100.0, 80.0, 0.0]
        }
        self.standard_tools = {
            "tools": [
                {
                    "tool_number": 1,
                    "name": "10mm Flat Endmill",
                    "type": "endmill",
                    "diameter_mm": 10.0,
                    "flute_length_mm": 15.0,
                    "overall_length_mm": 70.0,
                    "flute_count": 3,
                    "chipload_max": 0.08
                }
            ]
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mutation_1_planted_rapid_crash(self):
        """Mutation 1: Toolpaths with a planted rapid move through the stock."""
        gcode_path = os.path.join(self.temp_dir.name, "rapid_crash.gcode")
        with open(gcode_path, "w") as f:
            f.write("""
            G21 G90
            T1 M06
            S8000 M03
            G00 X50.0 Y40.0 Z5.0
            G00 Z-10.0  ; PLANTED RAPID MOVE INTO STOCK AT CUT DEPTH!
            G01 X60.0 F1000
            G00 Z5.0
            M30
            """)

        res = analyze_gcode_safety_and_kinematics(gcode_path, self.standard_tools, self.stock_bounds)
        self.assertFalse(res["is_safe"], "Checker must catch planted rapid crash")
        self.assertGreater(len(res["rapid_collisions"]), 0)
        self.assertEqual(res["rapid_collisions"][0]["to_xyz"][2], -10.0)

    def test_mutation_2_tool_longer_than_machine_z_travel(self):
        """Mutation 2: A tool longer than the machine's Z travel."""
        long_tools = {
            "tools": [
                {
                    "tool_number": 9,
                    "name": "Super Long Gun Drill",
                    "type": "drill",
                    "diameter_mm": 6.0,
                    "flute_length_mm": 120.0,
                    "overall_length_mm": 220.0,  # > Machine max 150mm
                    "chipload_max": 0.05
                }
            ]
        }
        gcode_path = os.path.join(self.temp_dir.name, "tool_too_long.gcode")
        with open(gcode_path, "w") as f:
            f.write("""
            G21 G90
            T9 M06
            S3000 M03
            G00 X20.0 Y20.0 Z5.0
            G01 Z-5.0 F200
            G00 Z5.0
            M30
            """)

        res = analyze_gcode_safety_and_kinematics(
            gcode_path, long_tools, self.stock_bounds,
            machine_limits={"z_travel_max_mm": 150.0, "z_travel_min_mm": -100.0}
        )
        self.assertFalse(res["is_safe"], "Checker must catch tool longer than machine Z travel")
        self.assertGreater(len(res["travel_violations"]), 0)
        tv = res["travel_violations"][0]
        self.assertEqual(tv["type"], "TOOL_EXCEEDS_Z_TRAVEL")
        self.assertEqual(tv["tool_length_mm"], 220.0)

    def test_mutation_3_stepdown_deeper_than_flute_length(self):
        """Mutation 3: A stepdown deeper than the flute length."""
        # Tool flute length is 15.0mm, but stepdown plunges 22.0mm in a single pass
        gcode_path = os.path.join(self.temp_dir.name, "excessive_stepdown.gcode")
        with open(gcode_path, "w") as f:
            f.write("""
            G21 G90
            T1 M06
            S8000 M03
            G00 X10.0 Y10.0 Z2.0
            G01 Z-22.0 F300  ; Stepdown of 22mm exceeds flute length of 15mm!
            G01 X40.0 F1200
            G00 Z5.0
            M30
            """)

        res = analyze_gcode_safety_and_kinematics(gcode_path, self.standard_tools, self.stock_bounds)
        self.assertFalse(res["is_safe"], "Checker must catch stepdown exceeding flute length")
        self.assertGreater(len(res["flute_violations"]), 0)
        fv = res["flute_violations"][0]
        self.assertEqual(fv["type"], "STEPDOWN_EXCEEDS_FLUTE_LENGTH")
        self.assertEqual(fv["flute_length_mm"], 15.0)
        self.assertGreater(fv["stepdown_mm"], 15.0)

    def test_mutation_4_finishing_pass_skips_face(self):
        """Mutation 4: A finishing pass that skips a face."""
        sim_path = os.path.join(self.temp_dir.name, "sim.json")
        with open(sim_path, "w") as f:
            json.dump({
                "ACCURACY_TUNED": {
                    "kinematics": {
                        "total_time_sec": 1200.0,
                        "rapid_collisions": [],
                        "travel_violations": [],
                        "flute_violations": [],
                        "is_safe": True,
                        "tools_used": [1]
                    }
                }
            }, f)

        dev_path = os.path.join(self.temp_dir.name, "dev.json")
        with open(dev_path, "w") as f:
            json.dump({
                "ACCURACY_TUNED": {
                    "mean_deviation_um": 120.0,
                    "floor_scallop_height_um": 25.0,
                    "volumetric_fidelity_pct": 72.0,  # Skipped a major face
                    "skipped_faces": [
                        {"face_id": "pocket_boss_face_3", "message": "Finishing pass skipped face pocket_boss_face_3"}
                    ],
                    "gouging_check": {"has_gouge": True, "status": "FAIL (SKIPPED_FACE)"}
                }
            }, f)

        tools_path = os.path.join(self.temp_dir.name, "tools.json")
        with open(tools_path, "w") as f:
            json.dump(self.standard_tools, f)

        critique = evaluate_run(
            sim_path=sim_path,
            deviations_path=dev_path,
            tools_path=tools_path
        )

        self.assertFalse(critique["converged"], "Checker must reject plan when finishing pass skips a face")
        strat_crit = critique["strategies"]["ACCURACY_TUNED"]
        self.assertFalse(strat_crit["converged"])
        violation_types = [v["type"] for v in strat_crit["violations"]]
        self.assertIn("SKIPPED_FACE", violation_types)


if __name__ == "__main__":
    unittest.main()
