#!/usr/bin/env python3
"""
Autonomous CNC Agent Execution Wrapper
======================================
Executes cnc_mc_agent/run_pipeline.py headlessly without modifying the agent codebase.
Saves run artifacts cleanly inside bench_sim/runs/<part_id>/.

Usage:
    python run_agent_wrapper.py --cad ../cadquery_code/exports/PART_10_benchmark_flange_housing.step
    python run_agent_wrapper.py --part 10
"""

import sys
import os
import argparse
import subprocess
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if (BASE_DIR.parent / "run_pipeline.py").exists():
    AGENT_DIR = BASE_DIR.parent
    CAD_DIR = BASE_DIR.parent.parent / "cadquery_code" / "exports"
    if not CAD_DIR.exists():
        CAD_DIR = BASE_DIR.parent / "input_files" / "step"
else:
    AGENT_DIR = BASE_DIR.parent / "cnc_mc_agent"
    CAD_DIR = BASE_DIR.parent / "cadquery_code" / "exports"

PIPELINE_SCRIPT = AGENT_DIR / "run_pipeline.py"
RUNS_DIR = BASE_DIR / "runs"

PARTS_CATALOG = {
    "01": "PART_01_plate_holes.step",
    "02": "PART_02_single_pocket.step",
    "03": "PART_03_pocket_with_holes.step",
    "04": "PART_04_stepped_pockets.step",
    "05": "PART_05_island_pocket.step",
    "06": "PART_06_refusal_tight_radius.step",
    "07": "PART_07_robotics_manifold_bracket.step",
    "08": "PART_08_dual_bearing_gearbox.step",
    "09": "PART_09_actuator_bracket.step",
    "10": "PART_10_benchmark_flange_housing.step",
}

def run_agent_for_part(step_path: Path, run_id: str = None, max_iterations: int = 1, force: bool = False):
    step_path = Path(step_path).resolve()
    if not step_path.exists():
        raise FileNotFoundError(f"CAD STEP file not found: {step_path}")

    if not run_id:
        part_name = step_path.stem.lower()
        run_id = f"agent_{part_name}"

    part_run_dir = RUNS_DIR / run_id
    balanced_ngc = part_run_dir / "3_balanced.ngc"
    cycle_time_ngc = part_run_dir / "1_cycle_time.ngc"
    accuracy_ngc = part_run_dir / "2_accuracy_tuned.ngc"
    refusal_json = part_run_dir / "refusal_notice.json"
    deviations_json = part_run_dir / "deviations.json"

    # Check cached run
    if balanced_ngc.exists() and not force:
        print(f"[*] Reusing existing Agent run: {balanced_ngc}")
        return {
            "status": "CACHED",
            "run_id": run_id,
            "run_dir": str(part_run_dir),
            "balanced_gcode": str(balanced_ngc),
            "cycle_time_gcode": str(cycle_time_ngc),
            "accuracy_gcode": str(accuracy_ngc),
            "refused": False
        }
    elif refusal_json.exists() and not force:
        print(f"[*] Reusing cached Agent Refusal Notice: {refusal_json}")
        with open(refusal_json) as f:
            r_data = json.load(f)
        return {
            "status": "REFUSED",
            "run_id": run_id,
            "run_dir": str(part_run_dir),
            "refused": True,
            "refusal_data": r_data
        }

    print(f"[>] Running CNC Agent Pipeline for: {step_path.name} (Run ID: {run_id})")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        "uv", "run", "python", str(PIPELINE_SCRIPT),
        "--cad", str(step_path),
        "--run-id", run_id,
        "--out-dir", str(RUNS_DIR),
        "--max-iterations", str(max_iterations)
    ]

    env = os.environ.copy()
    proc = subprocess.run(cmd, cwd=str(AGENT_DIR), capture_output=True, text=True, env=env)

    # Check for Machinability Refusal (Exit code 2)
    if proc.returncode == 2 or "FORMAL MACHINABILITY REFUSAL ISSUED" in proc.stdout:
        print(f" [✓] Agent formally issued machinability refusal notice for {step_path.name}")
        r_info = {}
        if refusal_json.exists():
            with open(refusal_json) as f:
                r_info = json.load(f)
        return {
            "status": "REFUSED",
            "run_id": run_id,
            "run_dir": str(part_run_dir),
            "refused": True,
            "refusal_data": r_info
        }

    if proc.returncode != 0 or not balanced_ngc.exists():
        print(f"[!] Error: Agent pipeline failed on {step_path.name}")
        print("STDERR:", proc.stderr[-1000:])
        return {
            "status": "FAILED",
            "run_id": run_id,
            "error": proc.stderr or proc.stdout
        }

    dev_data = {}
    if deviations_json.exists():
        with open(deviations_json) as f:
            dev_data = json.load(f)

    print(f" [✓] Agent G-code successfully generated for {step_path.name}!")
    return {
        "status": "SUCCESS",
        "run_id": run_id,
        "run_dir": str(part_run_dir),
        "balanced_gcode": str(balanced_ngc),
        "cycle_time_gcode": str(cycle_time_ngc),
        "accuracy_gcode": str(accuracy_ngc),
        "refused": False,
        "deviations": dev_data
    }


def main():
    parser = argparse.ArgumentParser(description="Autonomous CNC Agent Execution Wrapper")
    parser.add_argument("--cad", type=str, help="Path to arbitrary STEP model")
    parser.add_argument("--part", type=str, help="Part number from 01 to 10")
    parser.add_argument("--run-id", type=str, help="Optional custom Run ID")
    parser.add_argument("--force", action="store_true", help="Force re-running agent pipeline")
    parser.add_argument("--max-iterations", type=int, default=1, help="Max closed-loop feedback iterations")

    args = parser.parse_args()

    if args.cad:
        cad_path = Path(args.cad)
        res = run_agent_for_part(cad_path, args.run_id, args.max_iterations, args.force)
        print(json.dumps(res, indent=2))
    elif args.part:
        part_key = f"{int(args.part):02d}"
        if part_key not in PARTS_CATALOG:
            print(f"Unknown part number '{args.part}'. Available: 01 to 10")
            sys.exit(1)
        step_name = PARTS_CATALOG[part_key]
        cad_path = CAD_DIR / step_name
        res = run_agent_for_part(cad_path, args.run_id, args.max_iterations, args.force)
        print(json.dumps(res, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
