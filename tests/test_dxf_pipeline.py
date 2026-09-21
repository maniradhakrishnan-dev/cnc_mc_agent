#!/usr/bin/env python3
"""
Integration Test: 2D DXF Input Drawing Pipeline
Verifies that 2D DXF vector drawings can be ingested, converted to deterministic
machining features, planned into G-code, and simulated with full closed-loop auditing.
"""

import unittest
import os
import sys
import json
import tempfile
import subprocess

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DXF_SAMPLE = os.path.join(AGENT_DIR, "sample_part.dxf")


class TestDxfPipeline(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(DXF_SAMPLE), f"Sample DXF missing at {DXF_SAMPLE}")
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dxf_feature_extraction(self):
        """Test deterministic feature extraction from 2D DXF."""
        features_out = os.path.join(self.temp_dir.name, "dxf_features.json")
        cmd = [
            "python3",
            os.path.join(AGENT_DIR, "01_feature_extractor.py"),
            "--input", DXF_SAMPLE,
            "--output", features_out
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Feature extraction failed: {res.stderr}")
        self.assertTrue(os.path.exists(features_out))

        with open(features_out, "r") as f:
            data = json.load(f)

        self.assertIn("DXF", data.get("file_format", ""))
        feats = data.get("features", {})
        pockets = feats.get("pockets", []) or feats.get("setup_1_top_3axis", {}).get("pockets", [])
        holes = feats.get("holes", []) or feats.get("vertical_holes", []) or feats.get("setup_1_top_3axis", {}).get("vertical_holes", [])
        self.assertGreater(len(pockets), 0, "DXF should contain at least 1 pocket")
        self.assertGreater(len(holes), 0, "DXF should contain bolt circle / holes")

        # Verify stock requirements
        stock = data.get("stock_requirements", {})
        self.assertGreater(stock.get("x_length_mm", 0), 0)
        self.assertGreater(stock.get("y_length_mm", 0), 0)
        self.assertGreater(stock.get("z_length_mm", 0), 0)

    def test_dxf_strategy_planning(self):
        """Test strategy planner produces predictions and parameters for DXF features."""
        features_out = os.path.join(self.temp_dir.name, "dxf_features.json")
        subprocess.run([
            "python3",
            os.path.join(AGENT_DIR, "01_feature_extractor.py"),
            "--input", DXF_SAMPLE,
            "--output", features_out
        ], check=True)

        strategies_out = os.path.join(self.temp_dir.name, "strategies.json")
        tools_file = os.path.join(AGENT_DIR, "tool_library.json")

        cmd = [
            "python3",
            os.path.join(AGENT_DIR, "02_llm_planner.py"),
            "--features", features_out,
            "--tools", tools_file,
            "--out", strategies_out
        ]
        test_env = {**os.environ, "GEMINI_API_KEY": ""}
        res = subprocess.run(cmd, env=test_env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Strategy planner failed: {res.stderr}")
        self.assertTrue(os.path.exists(strategies_out))

        with open(strategies_out, "r") as f:
            strat_data = json.load(f)

        self.assertIn("strategies", strat_data)
        for key in ["CYCLE_TIME", "ACCURACY_TUNED", "BALANCED"]:
            self.assertIn(key, strat_data["strategies"])
            st = strat_data["strategies"][key]
            self.assertIn("predictions", st, f"Strategy {key} must have predictions block")
            self.assertIn("predicted_cycle_time_sec", st["predictions"])
            self.assertIn("predicted_mean_deviation_um", st["predictions"])


if __name__ == "__main__":
    unittest.main()
