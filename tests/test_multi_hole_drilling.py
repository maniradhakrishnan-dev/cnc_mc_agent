#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(TESTS_DIR)
CORE_DIR = os.path.join(AGENT_DIR, "core")

sys.path.insert(0, CORE_DIR)
from toolpath_generator import generate_gcode_for_strategy

class TestMultiHoleDrilling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        with open(os.path.join(AGENT_DIR, "tool_library.json")) as f:
            self.tool_lib = json.load(f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_multi_hole_drill_matching(self):
        # Create a feature spec with 4 different hole sizes matching tools in library:
        # Tool 8: 5.0mm drill
        # Tool 4: 6.6mm drill
        # Tool 9: 8.0mm drill
        # Tool 7: 10.0mm drill
        features = {
            "source_cad_file": "multi_hole_test.step",
            "stock_requirements": {
                "x_length_mm": 120.0,
                "y_length_mm": 100.0,
                "z_length_mm": 20.0,
                "bounds": {"min": [0, 0, 0], "max": [120, 100, 20]}
            },
            "features": {
                "setup_1_top_3axis": {
                    "pockets": [
                        {
                            "id": "pocket_1",
                            "type": "prismatic_pocket",
                            "bounds": {"min_x": 10.0, "max_x": 110.0, "min_y": 10.0, "max_y": 90.0, "width_x_mm": 100.0, "length_y_mm": 80.0},
                            "depth_from_external_top_mm": 3.0,
                            "floor_z_mm": 17.0,
                            "min_cavity_width_mm": 80.0,
                            "min_corner_radius_mm": 5.0,
                            "boundary_polygon_xy": [[10, 10], [110, 10], [110, 90], [10, 90], [10, 10]],
                            "island_polygons_xy": []
                        }
                    ],
                    "vertical_holes": [
                        {"id": "hole_5mm", "diameter_mm": 5.0, "radius_mm": 2.5, "center_xy_mm": [25.0, 25.0], "depth_from_external_top_mm": 15.0},
                        {"id": "hole_6_6mm", "diameter_mm": 6.6, "radius_mm": 3.3, "center_xy_mm": [75.0, 25.0], "depth_from_external_top_mm": 15.0},
                        {"id": "hole_8mm", "diameter_mm": 8.0, "radius_mm": 4.0, "center_xy_mm": [25.0, 75.0], "depth_from_external_top_mm": 15.0},
                        {"id": "hole_10mm", "diameter_mm": 10.0, "radius_mm": 5.0, "center_xy_mm": [75.0, 75.0], "depth_from_external_top_mm": 15.0}
                    ]
                }
            },
            "machinability_constraints": {
                "min_internal_corner_radius_mm": 5.0,
                "deepest_feature_depth_mm": 15.0,
                "all_floor_depths_mm": [3.0]
            }
        }

        strategy = {
            "name": "Balanced Multi-Hole Test",
            "tool_assignments": {
                "pocket_roughing": 1,
                "pocket_finishing": 2,
                "drilling": 4
            },
            "parameters": {
                "pocket_roughing": {"spindle_rpm": 8000, "feedrate_mm_min": 1400, "stepover_pct": 50, "stepdown_mm": 3.0},
                "pocket_finishing": {"enabled": True, "spindle_rpm": 10000, "feedrate_mm_min": 800, "stepover_pct": 30, "stepdown_mm": 3.0, "spring_passes": 0},
                "drilling": {"spindle_rpm": 4000, "feedrate_mm_min": 450, "peck_depth_mm": 3.0}
            }
        }

        output_ngc = os.path.join(self.temp_dir, "multi_hole.ngc")
        generate_gcode_for_strategy("BALANCED", strategy, features, self.tool_lib, output_ngc)

        self.assertTrue(os.path.exists(output_ngc))
        with open(output_ngc) as f:
            gcode = f.read()

        # Verify that all 4 distinct drill tools were called:
        # Tool 8 (5mm), Tool 4 (6.6mm), Tool 9 (8mm), Tool 7 (10mm)
        self.assertIn("T8 M06", gcode, "Tool T8 (5.0mm drill) should be called for 5mm hole")
        self.assertIn("T4 M06", gcode, "Tool T4 (6.6mm drill) should be called for 6.6mm hole")
        self.assertIn("T9 M06", gcode, "Tool T9 (8.0mm drill) should be called for 8mm hole")
        self.assertIn("T7 M06", gcode, "Tool T7 (10.0mm drill) should be called for 10mm hole")

        # Verify coordinate positioning for each hole
        self.assertIn("X25.000 Y25.000", gcode)
        self.assertIn("X75.000 Y25.000", gcode)
        self.assertIn("X25.000 Y75.000", gcode)
        self.assertIn("X75.000 Y75.000", gcode)

if __name__ == "__main__":
    unittest.main()
