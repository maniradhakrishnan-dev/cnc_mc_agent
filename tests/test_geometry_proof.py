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
GATE_B_WORKER = os.path.join(CORE_DIR, "_geometry_proof_worker.py")

class TestGeometryProofGateB(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_gate_b_pass_existing_run(self):
        cad_file = os.path.join(AGENT_DIR, "runs", "run_63c5ac82", "source_cad.step")
        stl_dir = os.path.join(AGENT_DIR, "runs", "run_63c5ac82")
        proof_json = os.path.join(self.temp_dir, "proof_b.json")

        cmd = ["freecadcmd", "--disable-addon", "RobustMCPBridge", GATE_B_WORKER, "--", cad_file, stl_dir, proof_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Gate B failed: {res.stdout}\n{res.stderr}")

        self.assertTrue(os.path.exists(proof_json))
        with open(proof_json) as f:
            proof = json.load(f)

        self.assertEqual(proof["status"], "PASS")
        self.assertIn("CYCLE_TIME", proof["strategies"])
        self.assertIn("ACCURACY_TUNED", proof["strategies"])
        self.assertIn("BALANCED", proof["strategies"])

        # Check cross strategy consistency
        self.assertTrue(len(proof["cross_strategy_consistency"]) > 0)
        for cs in proof["cross_strategy_consistency"]:
            self.assertTrue(cs["consistent"])

    def test_gate_b_fails_when_no_stls(self):
        cad_file = os.path.join(AGENT_DIR, "sample_part.step")
        empty_dir = self.temp_dir
        proof_json = os.path.join(self.temp_dir, "proof_empty.json")

        cmd = ["freecadcmd", "--disable-addon", "RobustMCPBridge", GATE_B_WORKER, "--", cad_file, empty_dir, proof_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Gate B should fail when no strategy STLs exist")

        with open(proof_json) as f:
            proof = json.load(f)
        self.assertEqual(proof["status"], "FAIL")

if __name__ == "__main__":
    unittest.main()
