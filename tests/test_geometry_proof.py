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
        runs_dir = os.path.join(AGENT_DIR, "runs")
        valid_run = None
        if os.path.exists(runs_dir):
            for r in sorted(os.listdir(runs_dir), reverse=True):
                r_path = os.path.join(runs_dir, r)
                cad_p = os.path.join(r_path, "source_cad.step")
                if os.path.isdir(r_path) and os.path.exists(cad_p):
                    if any(f.endswith(".stl") for f in os.listdir(r_path)):
                        valid_run = r_path
                        break
        if not valid_run:
            self.skipTest("No converged run with source_cad.step and STLs found to test Gate B")

        cad_file = os.path.join(valid_run, "source_cad.step")
        stl_dir = valid_run
        proof_json = os.path.join(self.temp_dir, "proof_b.json")

        cmd = ["freecadcmd", GATE_B_WORKER, "--pass", cad_file, stl_dir, proof_json]
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

        cmd = ["freecadcmd", GATE_B_WORKER, "--pass", cad_file, empty_dir, proof_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0, "Gate B should fail when no strategy STLs exist")

        with open(proof_json) as f:
            proof = json.load(f)
        self.assertEqual(proof["status"], "FAIL")

if __name__ == "__main__":
    unittest.main()
