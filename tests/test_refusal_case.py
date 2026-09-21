#!/usr/bin/env python3
"""
Unit Test: REQ.md Refusal Case (Line 41)
'A part with an internal corner radius smaller than the smallest tool in the library.
 No program can cut it. The agent must name the feature and the required tool diameter
 rather than producing a program that gouges the corner.'
"""

import unittest
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from run_pipeline import check_refusal_condition

class TestRefusalCase(unittest.TestCase):
    def test_refusal_triggered_on_tight_corners(self):
        # Tool library where smallest tool is 3.0mm (radius 1.5mm)
        tools = {
            "tools": [
                {"tool_number": 1, "name": "12mm Rougher", "diameter_mm": 12.0, "type": "endmill"},
                {"tool_number": 2, "name": "6mm Finisher", "diameter_mm": 6.0, "type": "endmill"},
                {"tool_number": 3, "name": "3mm Micro Endmill", "diameter_mm": 3.0, "type": "endmill"}
            ]
        }

        # CAD features where pocket_1 has a 0.8mm internal corner radius (requires tool diam <= 1.6mm)
        features = {
            "features": {
                "setup_1_top_3axis": {
                    "pockets": [
                        {
                            "id": "pocket_micro_corners",
                            "depth_mm": 5.0,
                            "internal_corner_radius_mm": 0.8
                        }
                    ]
                }
            }
        }

        notice = check_refusal_condition(features, tools)
        self.assertIsNotNone(notice, "Refusal should be triggered when corner radius < min tool radius")
        self.assertTrue(notice["refusal_triggered"])
        self.assertEqual(notice["status"], "REFUSED_UNMACHINABLE")
        self.assertEqual(notice["offending_feature_id"], "pocket_micro_corners")
        self.assertEqual(notice["internal_corner_radius_mm"], 0.8)
        self.assertEqual(notice["smallest_available_tool_diam_mm"], 3.0)
        self.assertEqual(notice["required_tool_diameter_mm"], 1.6)
        self.assertIn("severe corner gouging", notice["reason"])

    def test_no_refusal_when_tool_fits(self):
        tools = {
            "tools": [
                {"tool_number": 1, "name": "6mm Finisher", "diameter_mm": 6.0, "type": "endmill"},
                {"tool_number": 2, "name": "2mm Micro Endmill", "diameter_mm": 2.0, "type": "endmill"}
            ]
        }
        features = {
            "features": {
                "setup_1_top_3axis": {
                    "pockets": [
                        {
                            "id": "pocket_normal",
                            "depth_mm": 5.0,
                            "internal_corner_radius_mm": 2.5
                        }
                    ]
                }
            }
        }
        notice = check_refusal_condition(features, tools)
        self.assertIsNone(notice, "Refusal must NOT be triggered when a valid tool exists")

if __name__ == "__main__":
    unittest.main()
