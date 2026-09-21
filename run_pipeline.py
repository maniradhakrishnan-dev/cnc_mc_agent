#!/usr/bin/env python3
"""
Master Orchestrator: Autonomous Closed-Loop CNC Agent & Physical Verification Engine
Executes an end-to-end feedback loop from raw CAD drawing to physically verified Pareto Frontier:
1. Feature Extraction & B-Rep Topology Auditing
2. While (iteration <= max_iterations and not converged):
   a. LLM Strategy Planning (consuming diagnostic feedback from previous iteration)
   b. Z-Level B-Rep Slicing & G-Code Generation
   c. Headless CAMotics Simulation & Kinematic Collision Detection
   d. OpenCASCADE Metrological Surface Comparison
   e. Diagnostic Critique & Convergence Evaluation
3. Interactive Pareto Frontier Visual Reporting & History Tracking

Usage:
    uv run run_pipeline.py [--cad path/to/part.step] [--run-id my_run_01] [--max-iterations 3]
"""

import os
import sys
import time
import json
import shutil
import argparse
import subprocess
import uuid
from datetime import datetime

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_BASE_DIR = os.path.join(AGENT_DIR, "runs")

def run_step(step_num, step_name, script_name, args_list):
    script_path = os.path.join(AGENT_DIR, script_name)
    if shutil.which("uv"):
        cmd = ["uv", "run", script_path] + args_list
    else:
        cmd = [sys.executable, script_path] + args_list
    
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0
    
    if res.returncode != 0:
        print(f"\n[❌] PIPELINE ERROR at Step {step_num}: {step_name}")
        print(res.stdout)
        print(res.stderr)
        sys.exit(1)
        
    print(res.stdout.strip())
    print(f"⏱️ Step {step_num} completed in {elapsed:.2f}s\n")
    return res

def check_refusal_condition(features, tools):
    """
    Implements REQ.md Line 41 Refusal Case:
    'A part with an internal corner radius smaller than the smallest tool in the library.
     No program can cut it. The agent must name the feature and the required tool diameter
     rather than producing a program that gouges the corner.'
    """
    tool_list = tools.get("tools", [])
    cutters = [t for t in tool_list if t.get("diameter_mm", 0) > 0 and t.get("type", "") != "chamfer"]
    if not cutters:
        return None
    min_tool = min(cutters, key=lambda t: t["diameter_mm"])
    min_tool_diam = min_tool["diameter_mm"]
    min_tool_radius = min_tool_diam / 2.0

    feat_container = features.get("features", {})
    pockets = []
    if "setup_1_top_3axis" in feat_container:
        pockets = feat_container["setup_1_top_3axis"].get("pockets", [])
    elif "pockets" in feat_container:
        pockets = feat_container.get("pockets", [])
    elif "primary_setup_top_3axis" in feat_container:
        pockets = feat_container["primary_setup_top_3axis"].get("pockets", [])

    for p in pockets:
        r = p.get("internal_corner_radius_mm") or p.get("min_corner_radius_mm")
        if r is not None and r > 0.0:
            if r < (min_tool_radius - 0.05):
                return {
                    "refusal_triggered": True,
                    "status": "REFUSED_UNMACHINABLE",
                    "offending_feature_id": p.get("id", "pocket_with_tight_corner"),
                    "internal_corner_radius_mm": round(r, 3),
                    "smallest_available_tool_radius_mm": round(min_tool_radius, 3),
                    "smallest_available_tool_diam_mm": round(min_tool_diam, 3),
                    "required_tool_diameter_mm": round(r * 2.0, 3),
                    "reason": (
                        f"Refusal: Feature '{p.get('id', 'pocket')}' has an internal corner radius of {r:.2f}mm. "
                        f"The smallest cutting tool in library is Tool #{min_tool['tool_number']} ({min_tool['name']}) "
                        f"with diameter {min_tool_diam:.2f}mm (radius {min_tool_radius:.2f}mm). "
                        f"Machining cannot proceed without severe corner gouging."
                    ),
                    "resolution_instructions": (
                        f"Add an endmill with diameter <= {r * 2.0:.2f}mm to tool_library.json, "
                        f"or increase internal corner radii in the CAD model to >= {min_tool_radius:.2f}mm."
                    )
                }

    mc = features.get("machinability_constraints", {})
    global_min_r = mc.get("vertical_corner_radius_mm") or mc.get("min_internal_corner_radius_mm")
    if global_min_r is not None and global_min_r > 0.0:
        if global_min_r < (min_tool_radius - 0.05):
            return {
                "refusal_triggered": True,
                "status": "REFUSED_UNMACHINABLE",
                "offending_feature_id": "global_internal_corners",
                "internal_corner_radius_mm": round(global_min_r, 3),
                "smallest_available_tool_radius_mm": round(min_tool_radius, 3),
                "smallest_available_tool_diam_mm": round(min_tool_diam, 3),
                "required_tool_diameter_mm": round(global_min_r * 2.0, 3),
                "reason": (
                    f"Refusal: Part contains internal corner radius of {global_min_r:.2f}mm. "
                    f"Smallest tool in library is Tool #{min_tool['tool_number']} with diameter {min_tool_diam:.2f}mm. "
                    f"Machining cannot proceed without gouging."
                ),
                "resolution_instructions": (
                    f"Add an endmill with diameter <= {global_min_r * 2.0:.2f}mm to tool_library.json."
                )
            }

    return None

def run_doctor():
    """
    Pre-flight system diagnosis checking all environment dependencies, binaries,
    and configurations for turn-key deployment across different machines.
    """
    print("=" * 75)
    print(" 🩺 CNC AGENT PRE-FLIGHT SYSTEM DOCTOR")
    print("=" * 75)

    all_passed = True

    # 1. Python version
    py_ver = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 11)
    status_icon = "✓" if py_ok else "✗"
    color = "\033[1;32m" if py_ok else "\033[1;31m"
    print(f" [{status_icon}] Python Version     : {color}{py_ver}\033[0m (Required: >= 3.11)")
    if not py_ok:
        all_passed = False

    # 2. Package Manager (uv)
    uv_path = shutil.which("uv")
    if uv_path:
        try:
            uv_ver = subprocess.check_output([uv_path, "--version"], text=True).strip()
            print(f" [✓] Package Manager    : \033[1;32m{uv_ver}\033[0m ({uv_path})")
        except Exception:
            print(f" [✓] Package Manager    : \033[1;32mFound\033[0m ({uv_path})")
    else:
        print(" [!] Package Manager    : \033[1;33mNot found in PATH\033[0m (Recommended: install uv)")

    # 3. FreeCAD CLI (CAD kernel & feature extraction)
    fc_bin = shutil.which("freecadcmd") or shutil.which("freecad")
    if fc_bin:
        try:
            fc_out = subprocess.check_output([fc_bin, "-v"], stderr=subprocess.STDOUT, text=True).strip()
            fc_ver = fc_out.splitlines()[0] if fc_out else "Installed"
            print(f" [✓] CAD Kernel Engine  : \033[1;32m{fc_ver}\033[0m ({fc_bin})")
        except Exception:
            print(f" [✓] CAD Kernel Engine  : \033[1;32mFound\033[0m ({fc_bin})")
    else:
        print(" [✗] CAD Kernel Engine  : \033[1;31mfreecadcmd not found!\033[0m (Required for STEP analysis)")
        all_passed = False

    # 4. CAMotics / camsim (Voxel material removal simulator)
    camsim_bin = shutil.which("camsim") or shutil.which("camotics")
    if camsim_bin:
        try:
            cs_out = subprocess.check_output([camsim_bin, "--version"], stderr=subprocess.STDOUT, text=True).strip()
            cs_ver = cs_out.splitlines()[0] if cs_out else "Installed"
            print(f" [✓] Voxel Simulator    : \033[1;32m{cs_ver}\033[0m ({camsim_bin})")
        except Exception:
            print(f" [✓] Voxel Simulator    : \033[1;32mFound\033[0m ({camsim_bin})")
    else:
        print(" [✗] Voxel Simulator    : \033[1;31mcamsim not found!\033[0m (Required for material simulation)")
        all_passed = False

    # 5. Core Python Libraries
    libs = [
        ("pydantic", "Pydantic v2 Models"),
        ("shapely", "2D Computational Geometry"),
        ("trimesh", "3D Mesh Processing"),
        ("numpy", "Numerical Computing"),
        ("ezdxf", "DXF CAD Parser")
    ]
    for lib_name, desc in libs:
        try:
            mod = __import__(lib_name)
            ver = getattr(mod, "__version__", "OK")
            print(f" [✓] Library: {lib_name:<10}: \033[1;32mv{ver}\033[0m ({desc})")
        except ImportError:
            print(f" [✗] Library: {lib_name:<10}: \033[1;31mMissing\033[0m ({desc})")
            all_passed = False

    # 6. Gemini API Key
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
        print(f" [✓] LLM Cloud Planner  : \033[1;32mConfigured\033[0m (Key: {masked})")
    else:
        print(f" [i] LLM Cloud Planner  : \033[1;33mDeterministic fallback\033[0m (GEMINI_API_KEY not set)")

    print("=" * 75)
    if all_passed:
        print(" \033[1;32m[STATUS: SYSTEM READY]\033[0m All critical dependencies are verified.")
        print(" You can execute CNC Agent runs via: uv run cnc-agent <drawing.step/dxf>")
        print("=" * 75)
        sys.exit(0)
    else:
        print(" \033[1;31m[STATUS: SYSTEM DEFICIT]\033[0m Missing dependencies detected.")
        print(" Please resolve the above [✗] errors before executing pipelines.")
        print("=" * 75)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Closed-Loop CNC Agent Orchestrator")
    parser.add_argument("cad_pos", nargs="?", default=None, help="Input CAD file (positional)")
    parser.add_argument("--cad", default=None, help="Input CAD file (STEP or DXF)")
    parser.add_argument("--doctor", action="store_true", help="Run pre-flight dependency & system diagnostic checks")
    parser.add_argument("--tools", default=os.path.join(AGENT_DIR, "tool_library.json"), help="Tool library JSON")
    parser.add_argument("--run-id", default=None, help="Custom Run ID (default: random run_<8-hex>)")
    parser.add_argument("--out-dir", default=None, help="Optional parent directory for runs")
    parser.add_argument("--max-iterations", type=int, default=3, help="Maximum closed-loop feedback iterations (default: 3)")
    parser.add_argument("--target-accuracy-scallop-um", type=float, default=35.0, help="Target max floor scallop for accuracy strategy in µm")
    parser.add_argument("--target-balanced-scallop-um", type=float, default=75.0, help="Target max floor scallop for balanced strategy in µm")
    parser.add_argument("--target-fidelity-pct", type=float, default=98.0, help="Target minimum volumetric removal fidelity pct")
    args = parser.parse_args()

    if args.doctor:
        run_doctor()

    cad_arg = args.cad or args.cad_pos or os.path.join(AGENT_DIR, "sample_part.step")
    cad_file = os.path.abspath(cad_arg)
    tools_file = os.path.abspath(args.tools)
    
    if not os.path.exists(cad_file):
        print(f"[❌] Error: CAD input file not found: {cad_file}")
        sys.exit(1)

    # 1. Generate unique random Run ID and dedicated directory
    random_suffix = uuid.uuid4().hex[:8]
    run_id = args.run_id or f"run_{random_suffix}"
    
    parent_runs_dir = os.path.abspath(args.out_dir) if args.out_dir else RUNS_BASE_DIR
    run_dir = os.path.join(parent_runs_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)

    # 2. Archive source CAD model and tool library inside run directory
    cad_ext = os.path.splitext(cad_file)[1]
    archived_cad = os.path.join(run_dir, f"source_cad{cad_ext}")
    shutil.copy2(cad_file, archived_cad)

    archived_tools = os.path.join(run_dir, "tool_library.json")
    shutil.copy2(tools_file, archived_tools)

    features_json = os.path.join(run_dir, "features.json")
    history_json = os.path.join(run_dir, "iteration_history.json")
    report_html = os.path.join(run_dir, "frontier_report.html")
    metadata_json = os.path.join(run_dir, "run_metadata.json")

    print("=" * 90)
    print(" 🚀 AUTONOMOUS CLOSED-LOOP CNC AGENT & METROLOGICAL VERIFICATION")
    print("=" * 90)
    print(f" Run ID             : \033[1;33m{run_id}\033[0m")
    print(f" Target CAD Drawing : {cad_file}")
    print(f" Dedicated Run Dir  : {run_dir}")
    print(f" Max Iterations     : {args.max_iterations}")
    print(f" Target Scallop     : Accuracy ≤ {args.target_accuracy_scallop_um} µm | Balanced ≤ {args.target_balanced_scallop_um} µm")
    print("=" * 90 + "\n")

    t_total_start = time.time()

    # Step 1: Feature Extraction & 3-Tier Mathematical Audit (Run once)
    run_step(1, "Deterministic Feature Extraction", "01_feature_extractor.py", [
        "--input", cad_file,
        "--output", features_json
    ])

    # Refusal Check (REQ.md Line 41):
    # 'A part with an internal corner radius smaller than the smallest tool in the library.
    #  No program can cut it. The agent must name the feature and the required tool diameter
    #  rather than producing a program that gouges the corner.'
    with open(features_json) as f:
        features_data = json.load(f)
    with open(archived_tools) as f:
        tools_data = json.load(f)

    refusal_notice = check_refusal_condition(features_data, tools_data)
    if refusal_notice:
        refusal_path = os.path.join(run_dir, "refusal_notice.json")
        with open(refusal_path, "w") as f:
            json.dump(refusal_notice, f, indent=2)
        print("\n" + "!" * 90)
        print(" 🛑 FORMAL MACHINABILITY REFUSAL ISSUED (REQ.md Case)")
        print("!" * 90)
        print(f" Status        : \033[1;31m{refusal_notice['status']}\033[0m")
        print(f" Offending ID  : {refusal_notice['offending_feature_id']}")
        print(f" Internal R    : {refusal_notice['internal_corner_radius_mm']} mm")
        print(f" Smallest Tool : {refusal_notice['smallest_available_tool_diam_mm']} mm (Radius: {refusal_notice['smallest_available_tool_radius_mm']} mm)")
        print(f" Required Tool : Diameter <= {refusal_notice['required_tool_diameter_mm']} mm")
        print(f" Reason        : {refusal_notice['reason']}")
        print(f" Resolution    : {refusal_notice['resolution_instructions']}")
        print(f"\nSaved refusal notice to: {refusal_path}")
        print("!" * 90 + "\n")
        sys.exit(2)

    # Closed-Loop Iteration Loop
    iteration = 1
    converged = False
    feedback_file = None
    iteration_history = []
    best_iter_dir = None

    while iteration <= args.max_iterations:
        print("\n" + "#" * 90)
        print(f" 🔄 STARTING CLOSED-LOOP FEEDBACK ITERATION {iteration} OF {args.max_iterations}")
        print("#" * 90 + "\n")

        iter_dir = os.path.join(run_dir, f"iter_{iteration}")
        os.makedirs(iter_dir, exist_ok=True)
        best_iter_dir = iter_dir

        iter_strategies = os.path.join(iter_dir, "strategies.json")
        iter_sim = os.path.join(iter_dir, "simulation_results.json")
        iter_dev = os.path.join(iter_dir, "deviations.json")
        iter_critique = os.path.join(iter_dir, "critique.json")

        # Step 2: Gemini Strategy Planner (with feedback if iteration > 1)
        planner_args = [
            "--features", features_json,
            "--tools", archived_tools,
            "--output", iter_strategies
        ]
        if feedback_file and os.path.exists(feedback_file):
            planner_args += ["--feedback", feedback_file]

        run_step(f"2.{iteration}", f"Multi-Objective Strategy Planner [Iter {iteration}]", "02_llm_planner.py", planner_args)

        # Step 3: Toolpath & G-Code Generator
        run_step(f"3.{iteration}", f"Machine-Ready G-Code Generator [Iter {iteration}]", "03_toolpath_generator.py", [
            "--cad", cad_file,
            "--features", features_json,
            "--tools", archived_tools,
            "--strategies", iter_strategies,
            "--out-dir", iter_dir
        ])

        # Step 4: CAMotics Simulation & Rapid Safety Verifier
        run_step(f"4.{iteration}", f"CAMotics Simulation & Safety Verifier [Iter {iteration}]", "04_camotics_verifier.py", [
            "--features", features_json,
            "--tools", archived_tools,
            "--gcode-dir", iter_dir
        ])

        # Step 5: Surface Deviation & Metrological Verifier
        run_step(f"5.{iteration}", f"Surface Deviation & Metrological Verifier [Iter {iteration}]", "05_surface_comparator.py", [
            "--cad", cad_file,
            "--features", features_json,
            "--sim", iter_sim,
            "--strategies", iter_strategies,
            "--out", iter_dev
        ])

        # Step 5b: Diagnostic Critique Evaluator
        run_step(f"5b.{iteration}", f"Diagnostic Critique & Convergence Evaluator [Iter {iteration}]", "05b_critique_evaluator.py", [
            "--sim", iter_sim,
            "--deviations", iter_dev,
            "--tools", archived_tools,
            "--features", features_json,
            "--strategies", iter_strategies,
            "--out", iter_critique,
            "--target-accuracy-scallop-um", str(args.target_accuracy_scallop_um),
            "--target-balanced-scallop-um", str(args.target_balanced_scallop_um),
            "--target-fidelity-pct", str(args.target_fidelity_pct)
        ])

        # Read critique and deviations to assess convergence
        with open(iter_critique, "r") as f:
            critique_data = json.load(f)
        with open(iter_dev, "r") as f:
            dev_data = json.load(f)

        iter_summary = {
            "iteration": iteration,
            "converged": critique_data.get("converged", False),
            "verdict": critique_data.get("iteration_verdict", "UNKNOWN"),
            "total_violations": critique_data.get("total_violations", 0),
            "actionable_feedback": critique_data.get("actionable_feedback", []),
            "metrics": {
                k: {
                    "cycle_time": v.get("cycle_time_formatted"),
                    "mean_dev_um": v.get("mean_surface_deviation_um"),
                    "floor_scallop_um": v.get("floor_scallop_height_um"),
                    "fidelity_pct": v.get("volumetric_fidelity_pct")
                } for k, v in dev_data.items()
            }
        }
        iteration_history.append(iter_summary)

        if critique_data.get("converged", False):
            print("\n" + "=" * 90)
            print(f" 🎯 [CONVERGENCE ACHIEVED] All safety and quality tolerances satisfied at Iteration {iteration}!")
            print("=" * 90 + "\n")
            converged = True
            break
        else:
            print(f"\n[!] Iteration {iteration} completed with {critique_data.get('total_violations', 0)} quality/safety items flagged.")
            print(f"    Passing diagnostic critique to Iteration {iteration + 1} for autonomous self-correction...\n")
            feedback_file = iter_critique
            iteration += 1

    # Copy winning iteration artifacts to root run directory
    if best_iter_dir and os.path.exists(best_iter_dir):
        for fname in os.listdir(best_iter_dir):
            src_f = os.path.join(best_iter_dir, fname)
            dst_f = os.path.join(run_dir, fname)
            if os.path.isfile(src_f):
                shutil.copy2(src_f, dst_f)

    # Save complete iteration history
    with open(history_json, "w") as f:
        json.dump(iteration_history, f, indent=2)

    # Step 6: Interactive Pareto Report Generator
    run_step(6, "Pareto Frontier Report Generator", "06_report_generator.py", [
        "--features", os.path.join(run_dir, "features.json"),
        "--sim", os.path.join(run_dir, "simulation_results.json"),
        "--deviations", os.path.join(run_dir, "deviations.json"),
        "--history", history_json,
        "--out", report_html,
        "--run-id", run_id
    ])

    total_time = round(time.time() - t_total_start, 2)

    # Save comprehensive Run Metadata
    run_metadata = {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "source_cad_file": cad_file,
        "archived_cad_file": os.path.basename(archived_cad),
        "total_wall_clock_time_sec": total_time,
        "total_iterations_run": len(iteration_history),
        "converged": converged,
        "status": "CONVERGED_SUCCESS" if converged else "COMPLETED_MAX_ITERATIONS",
        "iteration_history": iteration_history,
        "artifacts": {
            "features": "features.json",
            "strategies": "strategies.json",
            "gcode_files": ["1_cycle_time.ngc", "2_accuracy_tuned.ngc", "3_balanced.ngc"],
            "camotics_projects": ["1_cycle_time.camotics", "2_accuracy_tuned.camotics", "3_balanced.camotics"],
            "cut_meshes": ["1_cycle_time_cut.stl", "2_accuracy_tuned_cut.stl", "3_balanced_cut.stl"],
            "simulation_results": "simulation_results.json",
            "metrology_deviations": "deviations.json",
            "critique": "critique.json",
            "iteration_history": "iteration_history.json",
            "interactive_report": "frontier_report.html"
        }
    }
    with open(metadata_json, "w") as f:
        json.dump(run_metadata, f, indent=2)

    # Final Executive Summary Table
    final_deviations_path = os.path.join(run_dir, "deviations.json")
    with open(final_deviations_path) as f:
        devs = json.load(f)

    print("=" * 105)
    status_label = "\033[1;32mCONVERGED\033[0m" if converged else "\033[1;33mCOMPLETED (MAX ITERATIONS)\033[0m"
    print(f" 🎉 RUN \033[1;33m{run_id}\033[0m COMPLETED in {total_time}s across {len(iteration_history)} iteration(s)! [{status_label}]")
    print("=" * 105)
    print(f"{'STRATEGY':<18} | {'CYCLE TIME':<12} | {'SCALLOP':<12} | {'MEAN DEV':<10} | {'FIDELITY':<10} | {'SAFETY CHECK'}")
    print("=" * 105)
    for k, v in devs.items():
        scallop_val = f"{v.get('floor_scallop_height_um', 0.0):.1f} µm"
        print(f"{k:<18} | {v['cycle_time_formatted']:<12} | {scallop_val:<12} | ±{v['mean_surface_deviation_um']:>4.1f} µm  | {v['volumetric_fidelity_pct']:>5.1f}%     | {v['gouging_check']['status']}")
    print("=" * 105)
    print(f"\n📂 All artifacts for Run ID [{run_id}] saved to:")
    print(f"   👉 \033[1;36m{run_dir}\033[0m")
    print(f"\n📄 Quick Commands for this run:")
    print(f"   • View Report   : xdg-open {report_html}")
    print(f"   • 3D Simulation : camotics {os.path.join(run_dir, '1_cycle_time.camotics')}")
    print(f"   • G-Code File   : {os.path.join(run_dir, '1_cycle_time.ngc')}")
    print("=" * 105)

if __name__ == "__main__":
    main()
