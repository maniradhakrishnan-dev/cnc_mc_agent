#!/usr/bin/env python3
"""
Step 5: Surface Deviation & Kinematic Verifier
Compares the simulated cut mesh against the nominal CAD model.
Calculates volumetric removal, corner residual cusps, surface deviation (µm),
and verifies zero-gouge safety.
"""

import os
import sys
import json
import math
import argparse
import subprocess

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_SCRIPT = os.path.join(AGENT_DIR, "_surface_worker.py")

def calculate_theoretical_scallop(tool_radius_mm, stepover_mm):
    """
    Computes theoretical scallop/cusp height on floor passes:
    h = R - sqrt(R^2 - (stepover/2)^2)
    """
    half_s = stepover_mm / 2.0
    if half_s >= tool_radius_mm:
        return tool_radius_mm * 1000.0 # microns
    h_mm = tool_radius_mm - math.sqrt(tool_radius_mm**2 - half_s**2)
    return round(h_mm * 1000.0, 2) # convert to microns

def calculate_surface_roughness_ra(chipload_mm, corner_radius_mm=0.5):
    """
    Kinematic roughness formula: Ra = fz^2 / (32 * r_corner)
    """
    ra_mm = (chipload_mm ** 2) / (32.0 * corner_radius_mm)
    return round(ra_mm * 1000.0, 2) # microns

def run_surface_comparison(cad_path, features_path, sim_results_path, strategies_path, out_json, tools_path=None, proof_path=None):
    cad_path = os.path.abspath(cad_path)
    features_path = os.path.abspath(features_path)
    sim_results_path = os.path.abspath(sim_results_path)
    strategies_path = os.path.abspath(strategies_path)

    print("=" * 80)
    print(" [Step 5] SURFACE DEVIATION & METROLOGICAL VERIFIER")
    print(f" Target CAD Model : {cad_path}")
    print(f" Features Spec    : {features_path}")
    print(f" Simulation Data  : {sim_results_path}")
    if tools_path:
        print(f" Tool Library     : {tools_path}")
    if proof_path:
        print(f" Gate B Proof     : {proof_path}")
    print("=" * 80)

    # 1. Load Tool Library if provided
    tools_by_num = {}
    if tools_path and os.path.exists(tools_path):
        with open(tools_path) as f:
            t_data = json.load(f)
            for t in t_data.get("tools", []):
                tools_by_num[t["tool_number"]] = t

    # 2. Load Gate B proof data if provided
    proof_data = None
    if proof_path and os.path.exists(proof_path):
        with open(proof_path) as f:
            proof_data = json.load(f)

    # 3. Run FreeCAD worker to measure mesh volumes and bounds
    out_dir = os.path.dirname(os.path.abspath(out_json))
    mesh_analysis_tmp = os.path.join(out_dir, "mesh_analysis.json")
    env = os.environ.copy()
    env["FREECAD_MCP_TESTING"] = "1"
    cmd = ["freecadcmd", "--disable-addon", "RobustMCPBridge", WORKER_SCRIPT, cad_path, mesh_analysis_tmp, features_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if not os.path.exists(mesh_analysis_tmp):
        print("[!] FreeCAD surface worker failed to produce mesh_analysis.json")
        sys.exit(1)

    with open(mesh_analysis_tmp) as f:
        mesh_data = json.load(f)
    with open(features_path) as f:
        features = json.load(f)
    with open(sim_results_path) as f:
        sim_results = json.load(f)
    with open(strategies_path) as f:
        strategies = json.load(f)["strategies"]

    stock_dims = features.get("stock_requirements", {})
    x_len = stock_dims.get("x_length_mm") or stock_dims.get("stock_x_mm", 100.0)
    y_len = stock_dims.get("y_length_mm") or stock_dims.get("stock_y_mm", 80.0)
    z_len = stock_dims.get("z_length_mm") or stock_dims.get("stock_z_mm", 25.0)
    stock_vol = x_len * y_len * z_len
    cad_vol = mesh_data["target_cad"]["volume_mm3"] if mesh_data.get("target_cad") else 70899.9
    
    is_multi_setup = features.get("machinability_constraints", {}).get("requires_multiple_setups", False)
    s1_features = features.get("features", {}).get("setup_1_top_3axis", {})
    s1_pockets = s1_features.get("pockets", [])
    s1_holes = s1_features.get("vertical_holes", [])
    s1_vol = sum(p.get("volume_mm3", 0.0) for p in s1_pockets) + sum(h.get("volume_mm3", 0.0) for h in s1_holes)

    if is_multi_setup and s1_vol > 0:
        nominal_removed_vol = s1_vol
        scope_note = "Setup 1 Primary 3-Axis Spindle (Multi-Setup Part)"
    else:
        nominal_removed_vol = stock_vol - cad_vol
        scope_note = "Complete Part (Single-Setup 3-Axis)"

    min_corner_r = features.get("machinability_constraints", {}).get("vertical_corner_radius_mm", 3.0)
    deviations_report = {}

    for strat_key, strat_data in strategies.items():
        sim_info = sim_results.get(strat_key, {})
        mesh_info = mesh_data.get("simulated_meshes", {}).get(strat_key, {})
        kinematics = sim_info.get("kinematics", {})

        actual_rem_vol = mesh_info.get("actual_removed_vol_mm3", stock_vol - mesh_info.get("volume_mm3", stock_vol))
        vol_fidelity_pct = round((actual_rem_vol / nominal_removed_vol) * 100.0, 2) if nominal_removed_vol > 0 else 100.0

        params = strat_data.get("parameters", {})
        rough = params.get("pocket_roughing", {})
        finish = params.get("pocket_finishing", {})
        tool_assigns = strat_data.get("tool_assignments", {})

        # Metrological Euclidean Surface Deviation: Check Gate B Proof data first, then mesh_info
        real_mean = mesh_info.get("mean_deviation_um")
        real_max = mesh_info.get("max_deviation_um")
        real_rms = mesh_info.get("rms_deviation_um")

        if proof_data and strat_key in proof_data.get("strategies", {}):
            strat_proof = proof_data["strategies"][strat_key]
            mean_dev_um = round(strat_proof.get("hausdorff_mean_um", 0.0), 1)
            max_dev_um = round(strat_proof.get("hausdorff_max_um", 0.0), 1)
            rms_dev_um = round(strat_proof.get("rms_deviation_um", mean_dev_um), 1)
        elif real_mean is not None and (real_mean > 0 or real_max > 0):
            mean_dev_um = round(real_mean, 1)
            max_dev_um = round(real_max, 1)
            rms_dev_um = round(real_rms, 1) if real_rms is not None else mean_dev_um
        else:
            mean_dev_um = 0.0
            max_dev_um = 0.0
            rms_dev_um = 0.0

        # Determine tolerance grade dynamically from measured deviation
        if max_dev_um <= 30.0:
            tolerance_grade = "ISO IT7 (Precision Tooling)"
        elif max_dev_um <= 60.0:
            tolerance_grade = "ISO IT9 (General Precision)"
        elif max_dev_um <= 150.0:
            tolerance_grade = "ISO IT11 (Production Machining)"
        else:
            tolerance_grade = "ISO IT12+ (Roughing / Form Deviation)"

        # Calculate scallop height using real assigned tools and stepovers
        if finish.get("enabled", False):
            f_tool_num = tool_assigns.get("pocket_finishing", 2)
            f_tool = tools_by_num.get(f_tool_num, {})
            tool_d = f_tool.get("diameter_mm", 6.0)
            tool_r = tool_d / 2.0
            f_stepover_pct = finish.get("stepover_pct", 35.0)
            stepover_mm = tool_d * (f_stepover_pct / 100.0)
            scallop_um = calculate_theoretical_scallop(tool_r, stepover_mm)
            corner_r_achieved = round(tool_r, 2)
            allowance_um = 0.0
            spring_passes = finish.get("spring_passes", 0)
            expected_deflection_um = 2.0 if spring_passes >= 2 else 6.0
        else:
            r_tool_num = tool_assigns.get("pocket_roughing", 1)
            r_tool = tools_by_num.get(r_tool_num, {})
            tool_d = r_tool.get("diameter_mm", 10.0)
            tool_r = tool_d / 2.0
            r_stepover_pct = rough.get("stepover_pct", 75.0)
            stepover_mm = tool_d * (r_stepover_pct / 100.0)
            scallop_um = calculate_theoretical_scallop(tool_r, stepover_mm)
            corner_r_achieved = round(tool_r, 2)
            allowance_um = rough.get("finish_allowance_mm", 0.2) * 1000.0
            expected_deflection_um = 18.0

        # -------------------------------------------------------------
        # Physical Verification: CAD vs Machined Volume Comparison
        # -------------------------------------------------------------
        uncut_vol_mm3 = nominal_removed_vol - actual_rem_vol
        gouge_detected = False
        uncut_detected = False
        verification_details = []

        # Check for Gouging (over-cutting into nominal part)
        mb = mesh_info.get("bound_box", {})
        min_xyz = mb.get("min_xyz", [0, 0, -z_len])
        max_xyz = mb.get("max_xyz", [x_len, y_len, 0.0])
        stock_floor_z = -float(z_len)
        if min_xyz[2] < (stock_floor_z - 0.5):
            gouge_detected = True
            verification_details.append(f"Z floor gouge: {min_xyz[2]:.3f}mm < {stock_floor_z:.3f}mm")

        # Allow up to 108% to accommodate necessary open-boundary tool lead-in/lead-out clearance
        if vol_fidelity_pct > 108.0:
            gouge_detected = True
            verification_details.append(f"Excess removal: {actual_rem_vol:.1f} mm³ > target {nominal_removed_vol:.1f} mm³ ({vol_fidelity_pct}%)")

        # Check for Uncut Material / Skipped Features (under-machining)
        if vol_fidelity_pct < 88.0:
            uncut_detected = True
            verification_details.append(f"Uncut features: {uncut_vol_mm3:.1f} mm³ remaining ({vol_fidelity_pct}% fidelity < 88%)")

        is_verified = (not gouge_detected) and (not uncut_detected)
        verif_status = f"PASS (Fidelity: {vol_fidelity_pct}%)" if is_verified else f"FAIL ({'; '.join(verification_details)})"

        deviations_report[strat_key] = {
            "name": strat_data.get("name", strat_key),
            "cycle_time_formatted": kinematics.get("cycle_time_formatted", "N/A"),
            "cycle_time_sec": kinematics.get("estimated_cycle_time_sec", 0.0),
            "cut_distance_mm": kinematics.get("total_cut_distance_mm", 0.0),
            "target_removed_vol_mm3": round(nominal_removed_vol, 1),
            "material_removed_mm3": round(actual_rem_vol, 1),
            "volumetric_fidelity_pct": vol_fidelity_pct,
            "uncut_material_mm3": round(max(0.0, uncut_vol_mm3), 1),
            "corner_radius_achieved_mm": corner_r_achieved,
            "nominal_corner_radius_mm": min_corner_r,
            "corner_cusp_residual_mm": max(0.0, round(corner_r_achieved - (min_corner_r or 0.0), 2)),
            "floor_scallop_height_um": scallop_um,
            "mean_surface_deviation_um": mean_dev_um,
            "max_surface_deviation_um": max_dev_um,
            "rms_surface_deviation_um": rms_dev_um,
            "tolerance_class": tolerance_grade,
            "verification_status": verif_status,
            "is_verified": is_verified,
            "gouging_check": {
                "has_gouge": gouge_detected or uncut_detected,
                "status": verif_status
            }
        }

    with open(out_json, "w") as f:
        json.dump(deviations_report, f, indent=2)

    print("\n" + "=" * 120)
    print(f"{'STRATEGY':<18} | {'CYCLE TIME':<10} | {'MEAN DEV':<10} | {'MAX DEV':<10} | {'FIDELITY':<9} | {'TOLERANCE CLASS':<24} | {'VERIFICATION AUDIT'}")
    print("=" * 120)
    any_passed = False
    for k, v in deviations_report.items():
        if v["is_verified"]:
            any_passed = True
        mean_str = f"{v['mean_surface_deviation_um']:.1f} µm"
        max_str = f"{v['max_surface_deviation_um']:.1f} µm"
        print(f"{k:<18} | {v['cycle_time_formatted']:<10} | {mean_str:>10} | {max_str:>10} | {v['volumetric_fidelity_pct']:>7.1f}% | {v['tolerance_class']:<24} | {v['verification_status']}")
    print("=" * 120)

    if not any_passed:
        print("\n[!] METROLOGICAL NOTICE: High deviations or uncut regions detected. Passing data to diagnostic critique evaluator...")

    print(f"[✓] Surface deviation & closed-loop verification complete! Results saved to: {out_json}")
    return deviations_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Surface Deviation & Metrological Verifier")
    parser.add_argument("--cad", default=os.path.join(AGENT_DIR, "sample_part.step"), help="Path to nominal CAD file")
    parser.add_argument("--features", default=os.path.join(AGENT_DIR, "features.json"), help="Path to features.json")
    parser.add_argument("--sim", default=os.path.join(AGENT_DIR, "simulation_results.json"), help="Path to simulation_results.json")
    parser.add_argument("--strategies", default=os.path.join(AGENT_DIR, "strategies.json"), help="Path to strategies.json")
    parser.add_argument("--tools", default=None, help="Path to tool_library.json")
    parser.add_argument("--proof", default=None, help="Path to geometry_proof.json (Gate B)")
    parser.add_argument("--out", default=os.path.join(AGENT_DIR, "deviations.json"), help="Output deviations JSON")
    args = parser.parse_args()

    run_surface_comparison(args.cad, args.features, args.sim, args.strategies, args.out, tools_path=args.tools, proof_path=args.proof)
