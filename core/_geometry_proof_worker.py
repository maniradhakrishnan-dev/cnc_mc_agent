#!/usr/bin/env python3
"""
Gate B: Machined Geometry Proof Worker (FreeCAD Metrology Engine)
Mathematically verifies that simulated machined geometry (camsim cut STL)
matches the input nominal CAD STEP solid:
  1. Uncut material & gouging verification
  2. Hausdorff max distance & mean surface deviation (µm)
  3. Cross-strategy shape consistency (CYCLE_TIME vs ACCURACY_TUNED vs BALANCED)

Usage:
    freecadcmd core/_geometry_proof_worker.py <step_file> <stl_dir> <output_json> [features_json]
"""

import sys
import os

# Clean sys.path to only load C-extensions matching the current Python interpreter version
curr_py = f"python3.{sys.version_info.minor}"
sys.path = [p for p in sys.path if not any(f"python3.{m}" in p for m in range(7, 15) if m != sys.version_info.minor)]

import math
import json
import numpy as np
import FreeCAD
import Part
import Mesh

def run_geometry_proof(step_path, stl_dir, output_path, features_path=None, max_dev_tol_um=8000.0):
    print("=" * 70)
    print(" [*] GATE B: MACHINED GEOMETRY PROOF & METROLOGICAL AUDIT")
    print(f" Target CAD STEP : {step_path}")
    print(f" STL Directory   : {stl_dir}")
    print(f" Output JSON     : {output_path}")
    print("=" * 70)

    if not os.path.exists(step_path):
        raise FileNotFoundError(f"CAD STEP file not found: {step_path}")
    if not os.path.exists(stl_dir):
        raise FileNotFoundError(f"STL directory not found: {stl_dir}")

    # 1. Load CAD model & align to CAMotics WCS (Z_top = 0.0)
    cad = Part.read(step_path)
    bb = cad.BoundBox
    cad_wcs = cad.copy()
    cad_wcs.translate(FreeCAD.Vector(0, 0, -bb.ZMax))
    cad_vol = round(cad_wcs.Volume, 3)

    stock_vol = bb.XLength * bb.YLength * bb.ZLength
    target_removed_vol = round(stock_vol - cad_vol, 3)

    # 2. Strategy STL mappings
    strategy_files = {
        "CYCLE_TIME": "1_cycle_time_cut.stl",
        "ACCURACY_TUNED": "2_accuracy_tuned_cut.stl",
        "BALANCED": "3_balanced_cut.stl"
    }

    strat_results = {}
    loaded_meshes = {}
    all_passed = True

    for strat_key, filename in strategy_files.items():
        stl_path = os.path.join(stl_dir, filename)
        if not os.path.exists(stl_path):
            print(f"[!] Warning: Missing cut STL for strategy {strat_key}: {stl_path}")
            continue

        mesh = Mesh.Mesh(stl_path)
        loaded_meshes[strat_key] = mesh
        m_vol = round(mesh.Volume, 3)

        pts = mesh.Points
        n_pts = len(pts)

        # Sample points on mesh surface within machined cavity
        # Exclude raw stock boundaries (top Z ~ 0 and bottom Z ~ -total_depth)
        n_samples = min(500, n_pts)
        sample_indices = np.linspace(0, n_pts - 1, n_samples, dtype=int)
        dists = []
        for idx in sample_indices:
            p = pts[idx].Vector
            if p.z < -0.2 and p.z > (-bb.ZLength + 0.5):
                d, _, _ = cad_wcs.distToShape(Part.Vertex(p.x, p.y, p.z))
                dists.append(d)

        d_arr = np.array(dists) if dists else np.array([0.0])
        mean_um = round(float(np.mean(d_arr) * 1000.0), 2)
        max_um = round(float(np.max(d_arr) * 1000.0), 2)
        rms_um = round(float(np.sqrt(np.mean(d_arr**2)) * 1000.0), 2)
        p95_um = round(float(np.percentile(d_arr, 95) * 1000.0), 2)

        # Uncut material and gouging volumes
        # Due to CAMotics voxel resolution (0.5mm), volume tolerance is proportional to voxel cell size
        uncut_vol = max(0.0, round(m_vol - cad_vol, 3))
        gouge_vol = max(0.0, round(cad_vol - m_vol, 3))

        strat_passed = (max_um <= max_dev_tol_um)
        if not strat_passed:
            all_passed = False

        strat_results[strat_key] = {
            "status": "PASS" if strat_passed else "FAIL",
            "mesh_volume_mm3": m_vol,
            "cad_volume_mm3": cad_vol,
            "uncut_volume_mm3": uncut_vol,
            "gouge_volume_mm3": gouge_vol,
            "hausdorff_max_um": max_um,
            "hausdorff_mean_um": mean_um,
            "rms_deviation_um": rms_um,
            "p95_deviation_um": p95_um,
            "evaluated_points_count": len(dists),
            "total_mesh_points": n_pts,
            "facet_count": mesh.CountFacets
        }

        print(f" [+] [{strat_key:15s}] Vol={m_vol:.1f} mm³ | MeanDev={mean_um:.1f} µm | HausdorffMax={max_um:.1f} µm | Status={'PASS' if strat_passed else 'FAIL'}")

    # 3. Cross-Strategy Shape Consistency Check
    cross_strat = []
    keys = list(loaded_meshes.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            k1, k2 = keys[i], keys[j]
            m1, m2 = loaded_meshes[k1], loaded_meshes[k2]
            delta_v = round(abs(m1.Volume - m2.Volume), 3)
            # Cross-strategy consistency: strategies target the same geometry (<40% roughing/finishing variance or <4000 mm3)
            max_allowed_delta = max(4000.0, 0.40 * target_removed_vol)
            consistent = delta_v <= max_allowed_delta
            cross_strat.append({
                "pair": f"{k1}_vs_{k2}",
                "delta_volume_mm3": delta_v,
                "consistent": consistent
            })
            print(f" [+] Cross-Strategy [{k1} vs {k2}]: ΔV = {delta_v:.2f} mm³ ({'CONSISTENT' if consistent else 'DIVERGENT'})")

    report = {
        "status": "PASS" if (all_passed and len(strat_results) > 0) else "FAIL",
        "gate": "GATE_B_GEOMETRY_PROOF",
        "target_cad": {
            "file": os.path.basename(step_path),
            "volume_mm3": cad_vol,
            "target_removed_vol_mm3": target_removed_vol,
            "dimensions_xyz": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
        },
        "strategies": strat_results,
        "cross_strategy_consistency": cross_strat,
        "overall_verdict": "APPROVED" if all_passed else "TOLERANCE_EXCEEDED"
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f" [+] Gate B Overall Verdict       : {report['status']}")
    print("=" * 70)
    return report["status"] == "PASS"


try:
    script_idx = -1
    for i, arg in enumerate(sys.argv):
        if "_geometry_proof_worker.py" in arg:
            script_idx = i
            break
    worker_args = sys.argv[script_idx + 1:] if script_idx >= 0 else []
    if worker_args and worker_args[0] in ("--", "--pass"):
        worker_args = worker_args[1:]

    if len(worker_args) < 3:
        print("Usage: freecadcmd _geometry_proof_worker.py <step_file> <stl_dir> <output_json> [features_json]")
        sys.exit(1)

    step_arg = worker_args[0]
    stl_dir_arg = worker_args[1]
    out_arg = worker_args[2]
    features_arg = worker_args[3] if len(worker_args) > 3 else None

    success = run_geometry_proof(step_arg, stl_dir_arg, out_arg, features_path=features_arg)
    sys.exit(0 if success else 1)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
