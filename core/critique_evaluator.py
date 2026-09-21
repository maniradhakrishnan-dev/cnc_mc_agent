#!/usr/bin/env python3
"""
Step 5b: Diagnostic Critique & Convergence Evaluator
Performs automated physical, kinematic, and metrological auditing on simulation
and deviation results. Identifies root causes of defects (scallops, cusps,
chipload overload, collisions) and generates structured, actionable CAM feedback
to drive closed-loop LLM re-planning.
"""

import os
import sys
import json
import math
import argparse

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

def calculate_recommended_stepover(tool_radius_mm, target_scallop_um):
    """
    Computes required stepover (mm) to achieve target scallop height (microns):
    h = R - sqrt(R^2 - (s/2)^2)  =>  (s/2)^2 = 2*R*h - h^2  =>  s = 2 * sqrt(2*R*h - h^2)
    """
    h_mm = target_scallop_um / 1000.0
    if h_mm >= tool_radius_mm:
        return tool_radius_mm
    inner = 2.0 * tool_radius_mm * h_mm - (h_mm ** 2)
    if inner <= 0:
        return 0.1
    s_mm = 2.0 * math.sqrt(inner)
    return min(s_mm, tool_radius_mm * 1.5)

def evaluate_run(sim_path, deviations_path, tools_path, features_path=None, strategies_path=None,
                 target_accuracy_scallop_um=35.0,
                 target_balanced_scallop_um=75.0,
                 target_fidelity_pct=98.0):
    """
    Evaluates simulation and deviation outputs against physical and quality thresholds.
    Also computes prediction-vs-result gap analysis as required by REQ.md Line 16.
    Returns:
        dict containing 'converged': bool, 'strategies': dict, and 'diagnostics': list
    """
    with open(sim_path, "r") as f:
        sim_data = json.load(f)

    with open(deviations_path, "r") as f:
        dev_data = json.load(f)

    tools = {}
    if os.path.exists(tools_path):
        with open(tools_path, "r") as f:
            t_data = json.load(f)
            for t in t_data.get("tools", []):
                tools[t["tool_number"]] = t

    features = {}
    min_internal_radius = None
    if features_path and os.path.exists(features_path):
        with open(features_path, "r") as f:
            features = json.load(f)
            feat_container = features.get("features", {})
            if "setup_1_top_3axis" in feat_container:
                pockets = feat_container["setup_1_top_3axis"].get("pockets", [])
            elif "primary_setup_top_3axis" in feat_container:
                pockets = feat_container["primary_setup_top_3axis"].get("pockets", [])
            elif "pockets" in feat_container:
                pockets = feat_container.get("pockets", [])
            else:
                pockets = features.get("pockets", [])

            for p in pockets:
                r = p.get("min_corner_radius_mm") or p.get("internal_corner_radius_mm")
                if r and (min_internal_radius is None or r < min_internal_radius):
                    min_internal_radius = r

            if min_internal_radius is None:
                mc = features.get("machinability_constraints", {})
                min_internal_radius = mc.get("vertical_corner_radius_mm") or mc.get("min_internal_corner_radius_mm")

    strategies_plan = {}
    if strategies_path and os.path.exists(strategies_path):
        with open(strategies_path, "r") as f:
            strategies_plan = json.load(f).get("strategies", {})

    critique = {
        "converged": True,
        "iteration_verdict": "CONVERGED",
        "total_violations": 0,
        "strategies": {},
        "prediction_gaps": {},
        "actionable_feedback": []
    }

    scallop_targets = {
        "CYCLE_TIME": 1500.0,
        "ACCURACY_TUNED": target_accuracy_scallop_um,
        "BALANCED": target_balanced_scallop_um
    }

    for strat_key, sim in sim_data.items():
        dev = dev_data.get(strat_key, {})
        kin = sim.get("kinematics", {})
        
        strat_critique = {
            "converged": True,
            "status": "PASS",
            "safety_pass": True,
            "quality_pass": True,
            "violations": [],
            "adjustments": {}
        }

        # 1. Check Rapid Traverse Collisions
        rapid_collisions = kin.get("rapid_collisions", [])
        if rapid_collisions:
            strat_critique["converged"] = False
            strat_critique["safety_pass"] = False
            strat_critique["violations"].append({
                "type": "RAPID_COLLISION",
                "severity": "CRITICAL",
                "count": len(rapid_collisions),
                "message": f"Detected {len(rapid_collisions)} rapid (G00) traverses inside stock bounds below clearance plane."
            })
            strat_critique["adjustments"]["clearance_z_mm"] = 5.0
            critique["actionable_feedback"].append(
                f"[{strat_key}] RAPID COLLISION: G00 moves collided inside stock boundary. Ensure Z clearance plane >= 5.0mm before any rapid traverse."
            )

        # 1b. Check Machine Travel Limit Violations (REQ.md Mutation 2)
        travel_violations = kin.get("travel_violations", [])
        if travel_violations:
            strat_critique["converged"] = False
            strat_critique["safety_pass"] = False
            for tv in travel_violations:
                strat_critique["violations"].append({
                    "type": "MACHINE_TRAVEL_EXCEEDED",
                    "severity": "CRITICAL",
                    "message": tv.get("message", "Tool or axis motion exceeds machine travel limits.")
                })
            tv_msg = f"[{strat_key}] TRAVEL LIMIT ({len(travel_violations)} moves): {travel_violations[0].get('message')}"
            if tv_msg not in critique["actionable_feedback"]:
                critique["actionable_feedback"].append(tv_msg)

        # 1c. Check Flute Length Violations (REQ.md Mutation 3)
        flute_violations = kin.get("flute_violations", [])
        if flute_violations:
            strat_critique["converged"] = False
            strat_critique["safety_pass"] = False
            for fv in flute_violations:
                strat_critique["violations"].append({
                    "type": "STEPDOWN_EXCEEDS_FLUTE_LENGTH",
                    "severity": "CRITICAL",
                    "message": fv.get("message", "Axial stepdown exceeds tool flute length.")
                })
            fv_msg = f"[{strat_key}] FLUTE LENGTH EXCEEDED ({len(flute_violations)} moves): {flute_violations[0].get('message')}"
            if fv_msg not in critique["actionable_feedback"]:
                critique["actionable_feedback"].append(fv_msg)

        # 2. Check Part Gouging & Skipped Faces (REQ.md Mutation 4)
        skipped_faces = dev.get("skipped_faces", [])
        if skipped_faces:
            strat_critique["converged"] = False
            strat_critique["quality_pass"] = False
            for sf in skipped_faces:
                strat_critique["violations"].append({
                    "type": "SKIPPED_FACE",
                    "severity": "CRITICAL",
                    "message": f"Finishing pass skipped face or feature ({sf.get('face_id', 'unknown')})."
                })
            sf_msg = f"[{strat_key}] SKIPPED FACE: Finishing toolpath omitted nominal geometry ({len(skipped_faces)} features)."
            if sf_msg not in critique["actionable_feedback"]:
                critique["actionable_feedback"].append(sf_msg)

        gouge_info = dev.get("gouging_check", {})
        if gouge_info.get("has_gouge", False):
            strat_critique["converged"] = False
            strat_critique["safety_pass"] = False
            strat_critique["violations"].append({
                "type": "PART_GOUGE",
                "severity": "CRITICAL",
                "message": "Toolpath invaded nominal CAD body beyond allowed tolerance."
            })
            gouge_msg = f"[{strat_key}] GOUGE DETECTED: Cut penetrated nominal CAD geometry. Increase finish allowance or verify tool radius offsets."
            if gouge_msg not in critique["actionable_feedback"]:
                critique["actionable_feedback"].append(gouge_msg)

        # 3. Check Chipload Overload
        max_chipload = kin.get("max_chipload_mm", 0.0)
        tools_used = kin.get("tools_used", [])
        for t_num in tools_used:
            t_spec = tools.get(t_num)
            if t_spec and "chipload_max" in t_spec:
                max_allowed = t_spec["chipload_max"] * 1.15
                if max_chipload > max_allowed:
                    overload_pct = round(((max_chipload / t_spec["chipload_max"]) - 1.0) * 100, 1)
                    strat_critique["violations"].append({
                        "type": "CHIPLOAD_OVERLOAD",
                        "severity": "WARNING",
                        "tool": t_num,
                        "measured_chipload": max_chipload,
                        "max_allowed": t_spec["chipload_max"],
                        "overload_pct": overload_pct
                    })
                    strat_critique["adjustments"]["reduce_feed_pct"] = round(overload_pct, 1)
                    critique["actionable_feedback"].append(
                        f"[{strat_key}] CHIPLOAD EXCEEDED: Tool #{t_num} reached {max_chipload:.4f} mm/tooth ({overload_pct}% above rating). Reduce roughing feedrate by {overload_pct}%."
                    )

        # 4. Check Floor Scallop Height (Quality Target)
        scallop_h = dev.get("floor_scallop_height_um", 0.0)
        target_h = scallop_targets.get(strat_key, 100.0)
        if scallop_h > target_h:
            strat_critique["quality_pass"] = False
            # For ACCURACY_TUNED, this prevents convergence
            if strat_key in ("ACCURACY_TUNED", "BALANCED"):
                strat_critique["converged"] = False
            
            finish_tool_num = tools_used[-1] if tools_used else 11
            finish_tool = tools.get(finish_tool_num, {})
            finish_tool_diam = finish_tool.get("diameter_mm", 6.0)
            finish_tool_radius = finish_tool_diam / 2.0
            
            req_stepover_mm = calculate_recommended_stepover(finish_tool_radius, target_h)
            req_stepover_pct = round((req_stepover_mm / finish_tool_diam) * 100.0, 1)

            strat_critique["violations"].append({
                "type": "EXCESSIVE_SCALLOP",
                "severity": "QUALITY_DEFICIT",
                "measured_scallop_um": scallop_h,
                "target_scallop_um": target_h,
                "recommended_stepover_pct": req_stepover_pct
            })
            strat_critique["adjustments"]["finish_stepover_pct"] = req_stepover_pct
            critique["actionable_feedback"].append(
                f"[{strat_key}] EXCESSIVE SCALLOP: Measured floor scallop is {scallop_h:.1f} µm (target: {target_h:.1f} µm). Reduce finish stepover to {req_stepover_pct}% with Tool #{finish_tool_num} ({finish_tool_diam}mm diam)."
            )

        # 5. Check Volumetric Fidelity
        vol_fidelity = dev.get("volumetric_fidelity_pct", 100.0)
        if vol_fidelity < target_fidelity_pct:
            strat_critique["converged"] = False
            strat_critique["quality_pass"] = False
            strat_critique["violations"].append({
                "type": "UNCUT_MATERIAL",
                "severity": "WARNING",
                "measured_fidelity_pct": vol_fidelity,
                "target_fidelity_pct": target_fidelity_pct
            })
            critique["actionable_feedback"].append(
                f"[{strat_key}] LOW VOLUMETRIC FIDELITY: Only {vol_fidelity:.1f}% of pocket volume cleared (target >= {target_fidelity_pct}%). Check for tight corners or increase stepdown overlap."
            )

        # 6. Check Corner Cusp Residual
        if min_internal_radius and strat_key == "ACCURACY_TUNED":
            finish_tool_num = tools_used[-1] if tools_used else 11
            finish_tool = tools.get(finish_tool_num, {})
            finish_tool_radius = finish_tool.get("diameter_mm", 6.0) / 2.0
            if finish_tool_radius > min_internal_radius + 0.1:
                cusp_msg = f"Pocket internal radius ({min_internal_radius:.1f}mm) is smaller than finish tool radius ({finish_tool_radius:.1f}mm)."
                strat_critique["violations"].append({
                    "type": "CORNER_CUSP_RESIDUAL",
                    "severity": "INFO",
                    "min_internal_radius_mm": min_internal_radius,
                    "finish_tool_radius_mm": finish_tool_radius,
                    "message": cusp_msg
                })
                # Find candidate smaller tool
                smaller_tools = [t for t in tools.values() if t["diameter_mm"] / 2.0 <= min_internal_radius]
                if smaller_tools:
                    best_sub = max(smaller_tools, key=lambda x: x["diameter_mm"])
                    strat_critique["adjustments"]["recommended_finishing_tool"] = best_sub["tool_number"]
                    critique["actionable_feedback"].append(
                        f"[{strat_key}] CORNER ACCESSIBILITY: Tool #{finish_tool_num} (R={finish_tool_radius:.1f}mm) cannot reach {min_internal_radius:.1f}mm internal pocket corners. Switch finishing tool to Tool #{best_sub['tool_number']} (Diam={best_sub['diameter_mm']}mm)."
                    )

        # 7. Prediction vs Result Gap Analysis (REQ.md Line 16)
        if strat_key in strategies_plan:
            st_plan = strategies_plan[strat_key]
            preds = st_plan.get("predictions", {})
            pred_time = preds.get("predicted_cycle_time_sec", 0.0)
            pred_dev = preds.get("predicted_mean_deviation_um", 0.0)
            pred_scallop = preds.get("predicted_max_scallop_um", 0.0)
            pred_chipload = preds.get("predicted_max_chipload_mm", 0.0)

            actual_time = kin.get("total_time_sec", 0.0)
            actual_dev = dev.get("mean_deviation_um", 0.0)
            actual_scallop = dev.get("floor_scallop_height_um", 0.0)
            actual_chipload = kin.get("max_chipload_mm", 0.0)

            time_err_pct = round(abs(actual_time - pred_time) / max(pred_time, 1e-6) * 100.0, 1) if pred_time > 0 else 0.0

            gap = {
                "cycle_time": {
                    "predicted_sec": pred_time,
                    "measured_sec": round(actual_time, 2),
                    "delta_sec": round(actual_time - pred_time, 2),
                    "error_pct": time_err_pct
                },
                "mean_deviation": {
                    "predicted_um": pred_dev,
                    "measured_um": round(actual_dev, 2),
                    "delta_um": round(actual_dev - pred_dev, 2)
                },
                "max_scallop": {
                    "predicted_um": pred_scallop,
                    "measured_um": round(actual_scallop, 2),
                    "delta_um": round(actual_scallop - pred_scallop, 2)
                },
                "max_chipload": {
                    "predicted_mm": pred_chipload,
                    "measured_mm": round(actual_chipload, 4),
                    "delta_mm": round(actual_chipload - pred_chipload, 4)
                }
            }
            strat_critique["prediction_gap"] = gap
            critique["prediction_gaps"][strat_key] = gap

        # Set final strategy status
        if not strat_critique["safety_pass"]:
            strat_critique["status"] = "FAIL_SAFETY"
        elif not strat_critique["quality_pass"]:
            strat_critique["status"] = "FAIL_TOLERANCE" if strat_key == "ACCURACY_TUNED" else "WARN_TOLERANCE"
        else:
            strat_critique["status"] = "PASS"

        if not strat_critique["converged"]:
            critique["converged"] = False
            critique["total_violations"] += len(strat_critique["violations"])

        critique["strategies"][strat_key] = strat_critique

    critique["iteration_verdict"] = "CONVERGED" if critique["converged"] else "NEEDS_REPLANNING"
    return critique

def main():
    parser = argparse.ArgumentParser(description="Diagnostic Critique & Convergence Evaluator")
    parser.add_argument("--sim", required=True, help="Path to simulation_results.json")
    parser.add_argument("--deviations", required=True, help="Path to deviations.json")
    parser.add_argument("--tools", required=True, help="Path to tool_library.json")
    parser.add_argument("--features", default=None, help="Path to features.json")
    parser.add_argument("--strategies", default=None, help="Path to strategies.json")
    parser.add_argument("--out", default=os.path.join(AGENT_DIR, "critique.json"), help="Output critique JSON")
    parser.add_argument("--target-accuracy-scallop-um", type=float, default=35.0)
    parser.add_argument("--target-balanced-scallop-um", type=float, default=75.0)
    parser.add_argument("--target-fidelity-pct", type=float, default=98.0)
    args = parser.parse_args()

    critique = evaluate_run(
        sim_path=args.sim,
        deviations_path=args.deviations,
        tools_path=args.tools,
        features_path=args.features,
        strategies_path=args.strategies,
        target_accuracy_scallop_um=args.target_accuracy_scallop_um,
        target_balanced_scallop_um=args.target_balanced_scallop_um,
        target_fidelity_pct=args.target_fidelity_pct
    )

    with open(args.out, "w") as f:
        json.dump(critique, f, indent=2)

    print("=" * 80)
    print(" 🔍 CLOSED-LOOP DIAGNOSTIC CRITIQUE EVALUATOR")
    verdict_str = "\033[1;32mCONVERGED [PASS]\033[0m" if critique["converged"] else "\033[1;31mNEEDS_REPLANNING [FAIL]\033[0m"
    print(f" Verdict          : {verdict_str}")
    print(f" Total Violations : {critique['total_violations']}")
    print("-" * 80)
    for strat, data in critique["strategies"].items():
        status_color = "\033[1;32m" if data["status"] == "PASS" else "\033[1;31m"
        print(f" • {strat:<16} : {status_color}{data['status']}\033[0m (Violations: {len(data['violations'])})")
        v_summary = {}
        for v in data["violations"]:
            k = f"[{v['severity']}] {v['type']}"
            v_summary[k] = v_summary.get(k, 0) + 1
        for k, count in v_summary.items():
            count_str = f" (x{count})" if count > 1 else ""
            print(f"   ↳ {k}{count_str}")
    
    if critique.get("prediction_gaps"):
        print("\n 📊 PREDICTION VS. RESULT GAP ANALYSIS (REQ.md Line 16):")
        print(f" {'Strategy':<16} | {'Metric':<16} | {'Predicted':<12} | {'Measured':<12} | {'Gap (Δ)':<12}")
        print(" " + "-" * 76)
        for strat, gaps in critique["prediction_gaps"].items():
            ct = gaps["cycle_time"]
            sc = gaps["max_scallop"]
            dev = gaps["mean_deviation"]
            cp = gaps["max_chipload"]
            print(f" {strat:<16} | {'Cycle Time':<16} | {str(ct['predicted_sec']) + 's':<12} | {str(ct['measured_sec']) + 's':<12} | {str(ct['delta_sec']) + 's (' + str(ct['error_pct']) + '%)':<12}")
            print(f" {'':<16} | {'Max Scallop':<16} | {str(sc['predicted_um']) + ' µm':<12} | {str(sc['measured_um']) + ' µm':<12} | {str(sc['delta_um']) + ' µm':<12}")
            print(f" {'':<16} | {'Mean Deviation':<16} | {str(dev['predicted_um']) + ' µm':<12} | {str(dev['measured_um']) + ' µm':<12} | {str(dev['delta_um']) + ' µm':<12}")
            print(f" {'':<16} | {'Max Chipload':<16} | {str(cp['predicted_mm']) + ' mm':<12} | {str(cp['measured_mm']) + ' mm':<12} | {str(cp['delta_mm']) + ' mm':<12}")
            print(" " + "-" * 76)

    if critique["actionable_feedback"]:
        print("\n 💡 Actionable Feedback for LLM Strategy Re-planning:")
        for fb in critique["actionable_feedback"][:8]:
            print(f"   👉 {fb}")
        if len(critique["actionable_feedback"]) > 8:
            print(f"   ... and {len(critique['actionable_feedback']) - 8} more diagnostic items in critique.json")

    print("=" * 80)
    print(f"[✓] Critique report saved to: {args.out}")

if __name__ == "__main__":
    main()
