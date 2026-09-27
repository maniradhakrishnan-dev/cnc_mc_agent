#!/usr/bin/env python3
"""
G-Code Benchmark Comparator
===========================
Rigorously compares two CNC G-code programs for the same part across:
  1. Total Cycle Time (Cutting vs. Rapid vs. Tool Change)
  2. Kinematic Motion Efficiency (Cut Distance vs. Air Distance)
  3. Retract Frequency & Smoothness
  4. Tool Change & Feedrate Profiles
"""

import sys
import re
import math
import argparse
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class GCodeMetrics:
    filename: str
    total_lines: int
    rapid_moves: int
    linear_moves: int
    arc_moves: int
    tool_changes: int
    
    rapid_distance_mm: float
    cutting_distance_mm: float
    total_distance_mm: float
    
    air_cut_ratio_pct: float
    retract_count: int
    
    rapid_time_sec: float
    cutting_time_sec: float
    tool_change_time_sec: float
    total_time_sec: float
    
    feedrates: List[float]
    spindle_speeds: List[float]
    tools_used: List[int]

def parse_gcode_kinematics(filepath: str, rapid_feed_mm_min: float = 5000.0, tool_change_sec: float = 12.0) -> GCodeMetrics:
    curr_x = 0.0
    curr_y = 0.0
    curr_z = 0.0
    curr_f = 1000.0
    curr_tool = 1
    
    motion_mode = "G00"
    absolute_mode = True
    metric_mode = True
    
    rapid_dist = 0.0
    cut_dist = 0.0
    rapid_time = 0.0
    cut_time = 0.0
    
    rapid_count = 0
    linear_count = 0
    arc_count = 0
    tool_change_count = 0
    retract_count = 0
    
    feedrates = set()
    spindle_speeds = set()
    tools = set()
    
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("%"):
            continue
            
        # Strip comments
        if ";" in line:
            line = line.split(";")[0].strip()
        line = re.sub(r"\(.*?\)", "", line).strip()
        if not line:
            continue
            
        # Parse modal states
        if "G90" in line:
            absolute_mode = True
        elif "G91" in line:
            absolute_mode = False
            
        if "G20" in line:
            metric_mode = False
        elif "G21" in line:
            metric_mode = True
            
        # Parse tool changes
        t_match = re.search(r"T(\d+)", line)
        if t_match:
            curr_tool = int(t_match.group(1))
            tools.add(curr_tool)
        if "M06" in line or "M6" in line:
            tool_change_count += 1
            
        # Parse Spindle and Feed
        s_match = re.search(r"S(\d+)", line)
        if s_match:
            spindle_speeds.add(float(s_match.group(1)))
            
        f_match = re.search(r"F([\d\.]+)", line)
        if f_match:
            curr_f = float(f_match.group(1))
            feedrates.add(curr_f)
            
        # Determine motion
        if "G00" in line or "G0 " in line or line == "G0":
            motion_mode = "G00"
        elif "G01" in line or "G1 " in line or line == "G1":
            motion_mode = "G01"
        elif "G02" in line or "G2 " in line or line == "G2":
            motion_mode = "G02"
        elif "G03" in line or "G3 " in line or line == "G3":
            motion_mode = "G03"
            
        # Extract target X, Y, Z
        x_match = re.search(r"X([-\d\.]+)", line)
        y_match = re.search(r"Y([-\d\.]+)", line)
        z_match = re.search(r"Z([-\d\.]+)", line)
        
        target_x = curr_x
        target_y = curr_y
        target_z = curr_z
        
        has_coord = False
        if x_match:
            target_x = float(x_match.group(1)) if absolute_mode else curr_x + float(x_match.group(1))
            has_coord = True
        if y_match:
            target_y = float(y_match.group(1)) if absolute_mode else curr_y + float(y_match.group(1))
            has_coord = True
        if z_match:
            target_z = float(z_match.group(1)) if absolute_mode else curr_z + float(z_match.group(1))
            has_coord = True
            
        if not has_coord:
            continue
            
        # Check Z Retract
        if target_z > curr_z and target_z >= 0.0 and curr_z < 0.0:
            retract_count += 1
            
        dx = target_x - curr_x
        dy = target_y - curr_y
        dz = target_z - curr_z
        dist = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        if dist > 0.0001:
            if motion_mode == "G00":
                rapid_count += 1
                rapid_dist += dist
                rapid_time += (dist / rapid_feed_mm_min) * 60.0
            elif motion_mode == "G01":
                linear_count += 1
                cut_dist += dist
                cut_time += (dist / max(curr_f, 10.0)) * 60.0
            elif motion_mode in ("G02", "G03"):
                arc_count += 1
                cut_dist += dist
                cut_time += (dist / max(curr_f, 10.0)) * 60.0
                
        curr_x, curr_y, curr_z = target_x, target_y, target_z

    total_dist = rapid_dist + cut_dist
    air_ratio = (rapid_dist / total_dist * 100.0) if total_dist > 0 else 0.0
    tc_time = tool_change_count * tool_change_sec
    total_time = rapid_time + cut_time + tc_time
    
    return GCodeMetrics(
        filename=filepath,
        total_lines=len(lines),
        rapid_moves=rapid_count,
        linear_moves=linear_count,
        arc_moves=arc_count,
        tool_changes=tool_change_count,
        rapid_distance_mm=round(rapid_dist, 2),
        cutting_distance_mm=round(cut_dist, 2),
        total_distance_mm=round(total_dist, 2),
        air_cut_ratio_pct=round(air_ratio, 2),
        retract_count=retract_count,
        rapid_time_sec=round(rapid_time, 2),
        cutting_time_sec=round(cut_time, 2),
        tool_change_time_sec=round(tc_time, 2),
        total_time_sec=round(total_time, 2),
        feedrates=sorted(list(feedrates)),
        spindle_speeds=sorted(list(spindle_speeds)),
        tools_used=sorted(list(tools))
    )

def format_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s"

def print_comparison_table(mA: GCodeMetrics, mB: GCodeMetrics, labelA: str = "Agent G-Code", labelB: str = "External CAM"):
    header = f"{'Metric':<35} | {labelA:<24} | {labelB:<24} | {'Difference / Analysis':<24}"
    sep = "-" * len(header)
    
    print("\n" + sep)
    print(f" CNC G-CODE BENCHMARK COMPARISON MATRIX")
    print(f" Target Part: ROB-ACT-7075-001")
    print(sep)
    print(header)
    print(sep)
    
    def row(name, valA, valB, diff_str=""):
        print(f"{name:<35} | {str(valA):<24} | {str(valB):<24} | {diff_str:<24}")

    row("Total G-Code Lines", mA.total_lines, mB.total_lines, f"{mA.total_lines - mB.total_lines:+d} lines")
    row("Total Machining Cycle Time", format_time(mA.total_time_sec), format_time(mB.total_time_sec), 
        f"{(mA.total_time_sec - mB.total_time_sec):+.1f}s ({((mA.total_time_sec/max(mB.total_time_sec,1))-1)*100:+.1f}%)")
    row("  - Cutting Feed Time", format_time(mA.cutting_time_sec), format_time(mB.cutting_time_sec), "")
    row("  - Rapid Traverse Time", format_time(mA.rapid_time_sec), format_time(mB.rapid_time_sec), "")
    row("  - Tool Change Overhead", format_time(mA.tool_change_time_sec), format_time(mB.tool_change_time_sec), "")
    print(sep)
    row("Total Toolpath Distance", f"{mA.total_distance_mm:.1f} mm", f"{mB.total_distance_mm:.1f} mm", f"{(mA.total_distance_mm - mB.total_distance_mm):+.1f} mm")
    row("  - Cutting Distance", f"{mA.cutting_distance_mm:.1f} mm", f"{mB.cutting_distance_mm:.1f} mm", "")
    row("  - Rapid Air Distance", f"{mA.rapid_distance_mm:.1f} mm", f"{mB.rapid_distance_mm:.1f} mm", "")
    row("Air-Cut Motion Ratio (%)", f"{mA.air_cut_ratio_pct:.1f}%", f"{mB.air_cut_ratio_pct:.1f}%", 
        f"Lower is more efficient")
    row("Total Z-Retract Count", mA.retract_count, mB.retract_count, f"{mA.retract_count - mB.retract_count:+d} retracts")
    print(sep)
    row("Tool Changes (M06)", mA.tool_changes, mB.tool_changes, "")
    row("Tools Utilized", str(mA.tools_used), str(mB.tools_used), "")
    row("Spindle Speed Range (RPM)", f"{mA.spindle_speeds}", f"{mB.spindle_speeds}", "")
    row("Feedrate Range (mm/min)", f"{min(mA.feedrates or [0])} - {max(mA.feedrates or [0])}", 
                                  f"{min(mB.feedrates or [0])} - {max(mB.feedrates or [0])}", "")
    print(sep + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="G-Code Benchmark Comparator")
    parser.add_argument("gcode_a", help="Path to first G-code file")
    parser.add_argument("gcode_b", help="Path to second G-code file")
    parser.add_argument("--label-a", default="Agent (Balanced)", help="Label for file A")
    parser.add_argument("--label-b", default="External CAM", help="Label for file B")
    parser.add_argument("--rapid-feed", type=float, default=5000.0, help="Machine rapid traverse rate (mm/min)")
    parser.add_argument("--tool-change-sec", type=float, default=12.0, help="Tool change time penalty (sec)")
    
    args = parser.parse_args()
    
    mA = parse_gcode_kinematics(args.gcode_a, args.rapid_feed, args.tool_change_sec)
    mB = parse_gcode_kinematics(args.gcode_b, args.rapid_feed, args.tool_change_sec)
    
    print_comparison_table(mA, mB, args.label_a, args.label_b)
