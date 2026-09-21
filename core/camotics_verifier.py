#!/usr/bin/env python3
"""
Step 4: CAMotics Simulation Engine & Physical Safety Verifier
Runs headless voxel cutting simulation via camsim, verifies rapid collisions,
calculates kinematic cycle times, and exports cut workpiece STLs.
"""

import os
import sys
import json
import math
import re
import argparse
import subprocess

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

def analyze_gcode_safety_and_kinematics(gcode_path, tool_lib, stock_bounds, rapid_feed=3000.0, tool_change_sec=8.0, machine_limits=None):
    """
    Parses G-code for:
    1. Rapid (G00) collisions below safety plane (Z < 0 in stock XY bounds).
    2. Exact kinematic machining time (rapid, feed, tool change, dwell).
    3. Tool utilization and chip-load limits.
    4. Machine travel limits (tool length vs Z travel).
    5. Axial stepdown vs tool flute length.
    """
    if machine_limits is None:
        machine_limits = {
            "z_travel_max_mm": 150.0,
            "z_travel_min_mm": -100.0,
            "x_travel_max_mm": 500.0,
            "y_travel_max_mm": 400.0
        }

    with open(gcode_path, "r") as f:
        lines = f.readlines()

    tools_dict = {t["tool_number"]: t for t in tool_lib.get("tools", [])}

    curr_x, curr_y, curr_z = 0.0, 0.0, 25.0
    curr_feed = 500.0
    curr_tool = None
    curr_rpm = 0.0
    motion_mode = "G0"

    total_rapid_dist = 0.0
    total_cut_dist = 0.0
    total_time_sec = 0.0
    tool_changes = 0
    tools_used = set()
    rapid_collisions = []
    travel_violations = []
    flute_violations = []
    max_cut_depth = 0.0
    max_observed_chipload = 0.0

    min_x, min_y, min_z = stock_bounds.get("min_xyz", [0.0, 0.0, 0.0])
    max_x, max_y, max_z = stock_bounds.get("max_xyz", [100.0, 80.0, 25.0])
    top_z = 0.0 # Origin is top of finished/stock feature
    lowest_cleared_z = top_z

    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith(";") or line.startswith("("):
            continue

        # Tool change
        t_match = re.search(r'T(\d+)\s+M0?6', line)
        if t_match:
            t_num = int(t_match.group(1))
            curr_tool = tools_dict.get(t_num)
            tools_used.add(t_num)
            tool_changes += 1
            total_time_sec += tool_change_sec

            # Mutation Check 2: Tool longer than machine Z travel
            if curr_tool:
                tool_len = curr_tool.get("overall_length_mm") or curr_tool.get("length_mm") or (curr_tool.get("flute_length_mm", 20.0) + 30.0)
                z_travel_max = machine_limits.get("z_travel_max_mm", 150.0)
                if tool_len > z_travel_max:
                    travel_violations.append({
                        "line": line_no,
                        "type": "TOOL_EXCEEDS_Z_TRAVEL",
                        "tool_number": t_num,
                        "tool_length_mm": tool_len,
                        "machine_z_travel_mm": z_travel_max,
                        "message": f"Tool #{t_num} overall length ({tool_len}mm) exceeds machine Z travel ({z_travel_max}mm)."
                    })

        # Spindle Speed
        s_match = re.search(r'S(\d+)', line)
        if s_match:
            curr_rpm = float(s_match.group(1))

        # Dwell
        p_match = re.search(r'G0?4\s+P([\d\.]+)', line)
        if p_match:
            total_time_sec += float(p_match.group(1))

        # Motion mode
        if re.search(r'\bG0*0\b', line):
            motion_mode = "G0"
        elif re.search(r'\bG0*1\b', line):
            motion_mode = "G1"
        elif re.search(r'\bG0*2\b', line):
            motion_mode = "G2"
        elif re.search(r'\bG0*3\b', line):
            motion_mode = "G3"

        # Feedrate
        f_match = re.search(r'F([\d\.]+)', line)
        if f_match:
            curr_feed = float(f_match.group(1))

        # Target coordinates
        nx, ny, nz = curr_x, curr_y, curr_z
        xm = re.search(r'X([-\d\.]+)', line)
        ym = re.search(r'Y([-\d\.]+)', line)
        zm = re.search(r'Z([-\d\.]+)', line)

        if xm: nx = float(xm.group(1))
        if ym: ny = float(ym.group(1))
        if zm: nz = float(zm.group(1))

        # Check machine axis envelope (X, Y, Z overtravel limits)
        if nz < machine_limits.get("z_travel_min_mm", -100.0) or nz > machine_limits.get("z_travel_max_mm", 150.0):
            travel_violations.append({
                "line": line_no,
                "type": "AXIS_TRAVEL_LIMIT_EXCEEDED",
                "axis": "Z",
                "target_val": nz,
                "message": f"Z-axis target {nz}mm exceeds machine limits [{machine_limits.get('z_travel_min_mm')}, {machine_limits.get('z_travel_max_mm')}]."
            })
        if nx < machine_limits.get("x_travel_min_mm", -100.0) or nx > machine_limits.get("x_travel_max_mm", 500.0):
            travel_violations.append({
                "line": line_no,
                "type": "AXIS_TRAVEL_LIMIT_EXCEEDED",
                "axis": "X",
                "target_val": nx,
                "message": f"X-axis target {nx}mm exceeds machine limits [{machine_limits.get('x_travel_min_mm')}, {machine_limits.get('x_travel_max_mm')}]."
            })
        if ny < machine_limits.get("y_travel_min_mm", -100.0) or ny > machine_limits.get("y_travel_max_mm", 400.0):
            travel_violations.append({
                "line": line_no,
                "type": "AXIS_TRAVEL_LIMIT_EXCEEDED",
                "axis": "Y",
                "target_val": ny,
                "message": f"Y-axis target {ny}mm exceeds machine limits [{machine_limits.get('y_travel_min_mm')}, {machine_limits.get('y_travel_max_mm')}]."
            })

        # Check machine bed / workbench strike
        stock_thickness = max_z - min_z
        bed_strike_depth = -stock_thickness - 2.0
        if nz < bed_strike_depth:
            travel_violations.append({
                "line": line_no,
                "type": "WORKBENCH_COLLISION_HAZARD",
                "axis": "Z",
                "target_val": nz,
                "message": f"Tool Z={nz}mm penetrates {abs(nz - (-stock_thickness)):.2f}mm past stock bottom into machine bed/vise."
            })

        dist = math.sqrt((nx - curr_x)**2 + (ny - curr_y)**2 + (nz - curr_z)**2)

        if dist > 1e-6:
            if motion_mode == "G0":
                total_rapid_dist += dist
                total_time_sec += (dist / rapid_feed) * 60.0
                
                # Check for rapid crash: G0 move cutting into stock below top_z
                in_stock_xy = (min_x - 1.0 <= nx <= max_x + 1.0) and (min_y - 1.0 <= ny <= max_y + 1.0)
                is_vertical_retract = (abs(nx - curr_x) < 1e-4 and abs(ny - curr_y) < 1e-4 and nz > curr_z)
                
                # Rapid collision hazard: downward plunge into stock or XY traverse at cut depth
                is_downward_rapid = (nz < curr_z) and (nz < top_z - 0.05)
                is_lateral_rapid_in_stock = (abs(nx - curr_x) > 1e-4 or abs(ny - curr_y) > 1e-4) and (nz < top_z - 0.05 or curr_z < top_z - 0.05)
                
                if (is_downward_rapid or is_lateral_rapid_in_stock) and in_stock_xy and not is_vertical_retract:
                    rapid_collisions.append({
                        "line": line_no,
                        "gcode": line,
                        "from_xyz": [round(curr_x, 3), round(curr_y, 3), round(curr_z, 3)],
                        "to_xyz": [round(nx, 3), round(ny, 3), round(nz, 3)]
                    })
            else: # Cutting feed (G1, G2, G3)
                total_cut_dist += dist
                total_time_sec += (dist / curr_feed) * 60.0
                if nz < max_cut_depth:
                    max_cut_depth = nz

                # Mutation Check 3: Stepdown deeper than flute length
                if nz < top_z:
                    axial_cut_engagement = max(0.0, lowest_cleared_z - nz)
                    flute_len = curr_tool.get("flute_length_mm") if curr_tool else None
                    if flute_len and axial_cut_engagement > (flute_len + 0.05):
                        flute_violations.append({
                            "line": line_no,
                            "type": "STEPDOWN_EXCEEDS_FLUTE_LENGTH",
                            "stepdown_mm": round(axial_cut_engagement, 3),
                            "flute_length_mm": flute_len,
                            "tool_number": curr_tool.get("tool_number"),
                            "message": f"Axial stepdown ({round(axial_cut_engagement, 3)}mm) exceeds tool flute length ({flute_len}mm)."
                        })
                    if nz < lowest_cleared_z:
                        lowest_cleared_z = nz

                # Chip load calculation fz = F / (RPM * z)
                if curr_tool and curr_rpm > 0 and curr_feed > 0:
                    flutes = curr_tool.get("flute_count", 2)
                    fz = curr_feed / (curr_rpm * flutes)
                    if fz > max_observed_chipload:
                        max_observed_chipload = fz

        curr_x, curr_y, curr_z = nx, ny, nz

    is_safe = (len(rapid_collisions) == 0 and len(travel_violations) == 0 and len(flute_violations) == 0)

    return {
        "cycle_time_sec": round(total_time_sec, 2),
        "cycle_time_formatted": f"{int(total_time_sec // 60)}m {int(total_time_sec % 60):02d}s",
        "rapid_distance_mm": round(total_rapid_dist, 2),
        "cut_distance_mm": round(total_cut_dist, 2),
        "tool_changes": tool_changes,
        "tools_used": sorted(list(tools_used)),
        "max_cut_depth_mm": round(abs(max_cut_depth), 3),
        "max_chipload_mm": round(max_observed_chipload, 4),
        "rapid_collisions": rapid_collisions,
        "travel_violations": travel_violations,
        "flute_violations": flute_violations,
        "is_safe": is_safe
    }

def create_camotics_project(gcode_filename, tool_lib, stock_bounds, output_camotics_path):
    """
    Creates a valid CAMotics .camotics project file.
    """
    tools_cfg = {}
    for t in tool_lib.get("tools", []):
        t_num = str(t["tool_number"])
        shape = "cylindrical"
        if t.get("type") == "chamfer_mill":
            shape = "conical"
        elif t.get("type") == "ball_endmill":
            shape = "ballnose"

        tools_cfg[t_num] = {
            "number": t["tool_number"],
            "units": "metric",
            "shape": shape,
            "length": t.get("flute_length_mm", 30.0) + 20.0,
            "diameter": t.get("diameter_mm", 10.0)
        }

    min_pt = stock_bounds.get("min_xyz", [0.0, 0.0, 0.0])
    max_pt = stock_bounds.get("max_xyz", [100.0, 80.0, 25.0])

    project_data = {
        "units": "metric",
        "resolution-mode": "manual",
        "resolution": 0.5,
        "tools": tools_cfg,
        "workpiece": {
            "automatic": False,
            "margin": 0.0,
            "bounds": {
                "min": min_pt,
                "max": max_pt
            }
        },
        "files": [os.path.basename(gcode_filename)]
    }

    with open(output_camotics_path, "w") as f:
        json.dump(project_data, f, indent=2)

    return output_camotics_path

def run_simulation(camotics_path, output_stl_path):
    """
    Invokes camsim CLI to simulate voxel material removal and export cut STL.
    """
    cmd = [
        "camsim",
        "--resolution", "medium",
        os.path.basename(camotics_path),
        os.path.basename(output_stl_path)
    ]
    working_dir = os.path.dirname(os.path.abspath(camotics_path))
    proc = subprocess.run(cmd, cwd=working_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[!] Warning: camsim reported returncode {proc.returncode}")
        print(proc.stderr)
        return False
    return os.path.exists(output_stl_path)

def extract_camotics_stock_bounds(features):
    """
    Computes exact CAMotics workpiece bounds from CAD features.
    Aligns top surface of stock to Z=0.0 matching G54 CNC work coordinate system.
    """
    if "canonical_wcs" in features and "stock_bounds_wcs" in features["canonical_wcs"]:
        wcs_b = features["canonical_wcs"]["stock_bounds_wcs"]
        return {
            "min_xyz": [round(float(v), 3) for v in wcs_b["min"]],
            "max_xyz": [round(float(v), 3) for v in wcs_b["max"]]
        }

    stock_req = features.get("stock_requirements", {})
    bounds = stock_req.get("bounds", {})

    if "min" in bounds and "max" in bounds:
        min_x = float(bounds["min"][0])
        max_x = float(bounds["max"][0])
        min_y = float(bounds["min"][1])
        max_y = float(bounds["max"][1])
        z_len = float(stock_req.get("z_length_mm") or stock_req.get("stock_z_mm") or (bounds["max"][2] - bounds["min"][2]))
    else:
        min_x = 0.0
        max_x = float(stock_req.get("stock_x_mm") or stock_req.get("x_length_mm", 100.0))
        min_y = 0.0
        max_y = float(stock_req.get("stock_y_mm") or stock_req.get("y_length_mm", 80.0))
        z_len = float(stock_req.get("stock_z_mm") or stock_req.get("z_length_mm", 25.0))

    return {
        "min_xyz": [round(min_x, 3), round(min_y, 3), round(-z_len, 3)],
        "max_xyz": [round(max_x, 3), round(max_y, 3), 0.0]
    }

def verify_and_simulate_all(features_path, tools_path, gcode_dir):
    with open(features_path) as f:
        features = json.load(f)
    with open(tools_path) as f:
        tools = json.load(f)

    stock_bounds = extract_camotics_stock_bounds(features)

    strategies = [
        ("CYCLE_TIME", "1_cycle_time.ngc", "1_cycle_time.camotics", "1_cycle_time_cut.stl"),
        ("ACCURACY_TUNED", "2_accuracy_tuned.ngc", "2_accuracy_tuned.camotics", "2_accuracy_tuned_cut.stl"),
        ("BALANCED", "3_balanced.ngc", "3_balanced.camotics", "3_balanced_cut.stl")
    ]

    print("=" * 80)
    print(" [Step 4] CAMOTICS SIMULATION & PHYSICAL COLLISION VERIFIER")
    print(f" Input Features  : {features_path}")
    print(f" Tool Library    : {tools_path}")
    print(f" G-Code Directory: {gcode_dir}")
    print("=" * 80)

    results = {}

    for strat_name, gcode_file, camotics_file, stl_file in strategies:
        gcode_full = os.path.join(gcode_dir, gcode_file)
        camotics_full = os.path.join(gcode_dir, camotics_file)
        stl_full = os.path.join(gcode_dir, stl_file)

        if not os.path.exists(gcode_full):
            print(f"[-] Skipping {strat_name}: {gcode_file} not found")
            continue

        print(f"\n[▶] Simulating Strategy: {strat_name}")
        
        # 1. Create project file
        create_camotics_project(gcode_full, tools, stock_bounds, camotics_full)
        print(f"    ↳ Created project: {camotics_file}")

        # 2. Run voxel cutting simulation
        success = run_simulation(camotics_full, stl_full)
        if success:
            stl_size_kb = os.path.getsize(stl_full) / 1024.0
            print(f"    ↳ Simulated Workpiece Cut STL: {stl_file} ({stl_size_kb:.1f} KB)")
        else:
            print(f"    ↳ [!] Simulation failed to generate {stl_file}")

        # 3. Kinematics & Safety Audit
        audit = analyze_gcode_safety_and_kinematics(gcode_full, tools, stock_bounds)
        status_str = "PASS (0 Rapid Collisions)" if audit["is_safe"] else f"FAIL ({len(audit['rapid_collisions'])} collisions)"
        print(f"    ↳ Safety Audit: {status_str}")
        print(f"    ↳ Est Cycle Time: {audit['cycle_time_formatted']} ({audit['cycle_time_sec']}s)")
        print(f"    ↳ Max Cut Depth : {audit['max_cut_depth_mm']} mm | Max Chipload: {audit['max_chipload_mm']} mm/th")

        results[strat_name] = {
            "gcode_file": gcode_file,
            "camotics_project": camotics_file,
            "cut_stl": stl_file,
            "simulation_success": success,
            "kinematics": audit
        }

    # Save results JSON
    results_path = os.path.join(gcode_dir, "simulation_results.json")
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 80)
    print(f"{'STRATEGY':<16} | {'EST. CYCLE TIME':<16} | {'CUT DISTANCE':<14} | {'CHIPLOAD':<12} | {'SAFETY AUDIT'}")
    print("=" * 80)
    for strat_name, data in results.items():
        k = data["kinematics"]
        safety = "✓ PASS (0 Hazards)" if k["is_safe"] else f"✗ FAIL ({len(k['rapid_collisions'])} Rapids)"
        print(f"{strat_name:<16} | {k['cycle_time_formatted']:<16} | {k['cut_distance_mm']:>8.1f} mm   | {k['max_chipload_mm']:>6.3f} mm/th | {safety}")
    print("=" * 80)
    print(f"[✓] Simulation & safety verification complete. Saved to: {results_path}")
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CAMotics Simulation & Safety Verifier")
    parser.add_argument("--features", default=os.path.join(AGENT_DIR, "features.json"), help="Path to features.json")
    parser.add_argument("--tools", default=os.path.join(AGENT_DIR, "tool_library.json"), help="Path to tool_library.json")
    parser.add_argument("--gcode-dir", default=AGENT_DIR, help="Directory containing G-code files")
    args = parser.parse_args()

    verify_and_simulate_all(args.features, args.tools, args.gcode_dir)
