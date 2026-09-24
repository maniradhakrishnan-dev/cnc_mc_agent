#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(TESTS_DIR)
CORE_DIR = os.path.join(AGENT_DIR, "core")
GATE_A_WORKER = os.path.join(CORE_DIR, "_extraction_proof_worker.py")
EXTRACTOR = os.path.join(CORE_DIR, "feature_extractor.py")

class TestExtractionProofGateA(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_gate_a_pass_sample_part(self):
        cad_file = os.path.join(AGENT_DIR, "sample_part.step")
        features_json = os.path.join(self.temp_dir, "features.json")
        proof_json = os.path.join(self.temp_dir, "proof.json")

        # 1. Extract features
        cmd = ["uv", "run", EXTRACTOR, "--input", cad_file, "--output", features_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Extraction failed: {res.stderr}")

        # 2. Run Gate A
        cmd = ["freecadcmd", GATE_A_WORKER, "--pass", cad_file, features_json, proof_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Gate A failed: {res.stdout}\n{res.stderr}")

        self.assertTrue(os.path.exists(proof_json))
        with open(proof_json) as f:
            proof = json.load(f)

        self.assertEqual(proof["status"], "PASS")
        self.assertGreater(proof["volumetric_fidelity_pct"], 99.0)
        self.assertLess(proof["total_residual_volume_mm3"], 10.0)

    def test_gate_a_pass_02_prismatic_pockets(self):
        cad_file = os.path.join(AGENT_DIR, "step", "02_prismatic_pockets_slots.step")
        features_json = os.path.join(self.temp_dir, "features_02.json")
        proof_json = os.path.join(self.temp_dir, "proof_02.json")

        cmd = ["uv", "run", EXTRACTOR, "--input", cad_file, "--output", features_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        cmd = ["freecadcmd", GATE_A_WORKER, "--pass", cad_file, features_json, proof_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        with open(proof_json) as f:
            proof = json.load(f)

        self.assertEqual(proof["status"], "PASS")
        self.assertGreaterEqual(proof["volumetric_fidelity_pct"], 98.0)

    def test_gate_a_fails_when_feature_omitted(self):
        cad_file = os.path.join(AGENT_DIR, "sample_part.step")
        features_json = os.path.join(self.temp_dir, "features.json")
        proof_json = os.path.join(self.temp_dir, "proof_fail.json")

        cmd = ["uv", "run", EXTRACTOR, "--input", cad_file, "--output", features_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

        # Corrupt features: remove the main pocket
        with open(features_json, "r") as f:
            feat_data = json.load(f)
        feat_data["features"]["setup_1_top_3axis"]["pockets"] = []
        with open(features_json, "w") as f:
            json.dump(feat_data, f, indent=2)

        # Run Gate A -> must fail!
        cmd = ["freecadcmd", GATE_A_WORKER, "--pass", cad_file, features_json, proof_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Gate A should have failed on omitted pocket")

        with open(proof_json) as f:
            proof = json.load(f)
        self.assertEqual(proof["status"], "FAIL")
        self.assertGreater(proof["unmatched_volume_mm3"], 5000.0)

if __name__ == "__main__":
    unittest.main()
