"""Automated Regression & Performance Benchmark Suite for CNC Agent.

Iterates through test parts in input_files/step/ to verify:
1. Feature Extraction & B-Rep Topology Proof
2. Refusal condition check (e.g. tight radii)
3. G-code synthesis & physical CAMotics simulation
4. Metrological surface comparison & Pareto trade-offs

Usage:
    uv run python benchmark.py                   # Run all benchmark parts
    uv run python benchmark.py 01 02             # Run specific parts
"""

import os
import sys
import time
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input_files" / "step"

BENCHMARK_PARTS = [
    ("PART_01_plate_holes.step", "4x Through-Holes (Drilling Audit)", False),
    ("PART_02_single_pocket.step", "Filleted Pocket (Clearing & Finish)", False),
    ("PART_03_pocket_with_holes.step", "Pocket with Floor Holes (Sequencing)", False),
    ("PART_04_stepped_pockets.step", "Stepped Z-Level Pockets (Transitions)", False),
    ("PART_05_island_pocket.step", "Pocket with Island Boss (Annular)", False),
    ("PART_06_refusal_tight_radius.step", "R1.0mm Tight Fillet (Refusal Case)", True),
    ("PART_07_robotics_manifold_bracket.step", "55+ Face Aerospace Manifold", False),
    ("PART_08_dual_bearing_gearbox.step", "60+ Face Dual-Bearing Housing", False),
]


def run_benchmark():
    args = sys.argv[1:]
    selected_parts = []
    
    if args:
        for fname, desc, expect_refusal in BENCHMARK_PARTS:
            if any(arg in fname for arg in args):
                selected_parts.append((fname, desc, expect_refusal))
    else:
        selected_parts = BENCHMARK_PARTS

    print("=" * 95)
    print(" 🚀 RUNNING CNC AGENT REGRESSION & BENCHMARK MATRIX")
    print("=" * 95)
    print(f" Total Benchmark Geometries: {len(selected_parts)}")
    print("=" * 95 + "\n")

    results = []

    for idx, (fname, desc, expect_refusal) in enumerate(selected_parts, 1):
        cad_path = INPUT_DIR / fname
        if not cad_path.exists():
            print(f"[!] Warning: Missing CAD file {cad_path}")
            continue

        print(f"[{idx}/{len(selected_parts)}] Benchmarking {fname} ({desc})...")
        t0 = time.time()
        cmd = ["uv", "run", "run_pipeline.py", "--cad", str(cad_path), "--max-iterations", "1"]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
        elapsed = time.time() - t0

        if expect_refusal:
            passed = proc.returncode == 2 and "FORMAL MACHINABILITY REFUSAL ISSUED" in proc.stdout
            status_str = "PASS (REFUSED AS EXPECTED)" if passed else "FAIL (DID NOT REFUSE)"
        else:
            passed = proc.returncode == 0
            status_str = "PASS (VERIFIED)" if passed else "FAIL (ERROR)"

        results.append({
            "part": fname,
            "desc": desc,
            "elapsed_s": round(elapsed, 2),
            "status": status_str,
            "passed": passed
        })
        print(f"     ↳ Result: {status_str} in {elapsed:.2f}s\n")

    print("\n" + "=" * 95)
    print(" 📊 BENCHMARK REGRESSION SUMMARY TABLE")
    print("=" * 95)
    print(f"{'PART NAME':<40} | {'ELAPSED':<10} | {'BENCHMARK RESULT'}")
    print("=" * 95)
    all_ok = True
    for r in results:
        if not r["passed"]:
            all_ok = False
        print(f"{r['part']:<40} | {r['elapsed_s']:>6.2f}s    | {r['status']}")
    print("=" * 95)

    if all_ok:
        print("\n✅ ALL BENCHMARK PARTS PASSED REGRESSION AUDIT!\n")
    else:
        print("\n⚠️ SOME BENCHMARK PARTS FLAGGED ISSUES - REVIEW RUN LOGS.\n")


if __name__ == "__main__":
    run_benchmark()
