#!/usr/bin/env python3
"""
Headless FreeCAD CAM Baseline Generator CLI
============================================
Generates standard FreeCAD CAM toolpaths (.ngc) for benchmark geometries headlessly.

Usage:
    python generate_freecad_baseline.py --cad ../cadquery_code/exports/PART_10_benchmark_flange_housing.step
    python generate_freecad_baseline.py --part 10
    python generate_freecad_baseline.py --all
"""

import sys
import os
import argparse
import subprocess
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKER_SCRIPT = BASE_DIR / "_freecad_worker.py"
BASELINES_DIR = BASE_DIR / "baselines"

if (BASE_DIR.parent / "run_pipeline.py").exists():
    CAD_DIR = BASE_DIR.parent.parent / "cadquery_code" / "exports"
    if not CAD_DIR.exists():
        CAD_DIR = BASE_DIR.parent / "input_files" / "step"
else:
    CAD_DIR = BASE_DIR.parent / "cadquery_code" / "exports"

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

def generate_baseline_for_step(step_path: Path, output_ngc: Path, tool_dia: float = 10.0, stepdown: float = 4.0, force: bool = False):
    step_path = Path(step_path).resolve()
    output_ngc = Path(output_ngc).resolve()

    if not step_path.exists():
        raise FileNotFoundError(f"CAD STEP file not found: {step_path}")

    if output_ngc.exists() and not force:
        print(f"[*] Reusing existing FreeCAD baseline: {output_ngc}")
        return {
            "status": "CACHED",
            "output_ngc": str(output_ngc),
            "step_path": str(step_path)
        }

    print(f"[>] Generating FreeCAD CAM baseline for: {step_path.name}")
    output_ngc.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["FREECAD_MCP_TESTING"] = "1"

    cmd = [
        "freecadcmd",
        str(WORKER_SCRIPT),
        "--pass",
        str(step_path),
        str(output_ngc),
        str(tool_dia),
        str(stepdown)
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    
    if res.returncode != 0 or not output_ngc.exists():
        print(f"[!] Error generating FreeCAD baseline for {step_path.name}")
        print("STDERR:", res.stderr)
        print("STDOUT:", res.stdout)
        return {
            "status": "FAILED",
            "error": res.stderr or res.stdout,
            "step_path": str(step_path)
        }

    summary = {"status": "SUCCESS", "output_ngc": str(output_ngc), "step_path": str(step_path)}
    for line in res.stdout.splitlines():
        if "__BASELINE_RESULT__" in line:
            try:
                json_str = line.split("__BASELINE_RESULT__")[-1].strip()
                summary = json.loads(json_str)
            except Exception:
                pass
            break

    print(f" [✓] FreeCAD CAM baseline generated: {output_ngc.name} ({summary.get('total_gcode_lines', 0)} lines)")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Generate FreeCAD CAM Baselines Headlessly")
    parser.add_argument("--cad", type=str, help="Path to arbitrary STEP model")
    parser.add_argument("--out", type=str, help="Path to destination .ngc file")
    parser.add_argument("--part", type=str, help="Part number from 01 to 10")
    parser.add_argument("--all", action="store_true", help="Generate baselines for all parts 01 to 10")
    parser.add_argument("--force", action="store_true", help="Overwrite existing baseline files")
    parser.add_argument("--tool-dia", type=float, default=10.0, help="Tool diameter in mm (default: 10.0)")
    parser.add_argument("--stepdown", type=float, default=4.0, help="Axial stepdown per pass in mm (default: 4.0)")

    args = parser.parse_args()

    if args.cad:
        cad_path = Path(args.cad)
        out_path = Path(args.out) if args.out else BASELINES_DIR / f"{cad_path.stem}_freecad.ngc"
        res = generate_baseline_for_step(cad_path, out_path, args.tool_dia, args.stepdown, args.force)
        print(json.dumps(res, indent=2))
    elif args.part:
        part_key = f"{int(args.part):02d}"
        if part_key not in PARTS_CATALOG:
            print(f"Unknown part number '{args.part}'. Available: 01 to 10")
            sys.exit(1)
        step_name = PARTS_CATALOG[part_key]
        cad_path = CAD_DIR / step_name
        out_path = BASELINES_DIR / f"part_{part_key}_freecad.ngc"
        res = generate_baseline_for_step(cad_path, out_path, args.tool_dia, args.stepdown, args.force)
        print(json.dumps(res, indent=2))
    elif args.all:
        print("=" * 75)
        print(" Generating FreeCAD CAM Baselines for All Benchmark Parts (01-10)")
        print("=" * 75)
        results = []
        for key, step_name in sorted(PARTS_CATALOG.items()):
            cad_path = CAD_DIR / step_name
            out_path = BASELINES_DIR / f"part_{key}_freecad.ngc"
            res = generate_baseline_for_step(cad_path, out_path, args.tool_dia, args.stepdown, args.force)
            results.append((key, res))
        print("=" * 75)
        print(" Batch Generation Complete!")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
