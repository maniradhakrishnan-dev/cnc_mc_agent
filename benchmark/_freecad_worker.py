#!/usr/bin/env python3
"""
FreeCAD CAM Worker: Headless Toolpath Generator for Benchmark Baselines
======================================================================
Executes inside freecadcmd to:
  1. Load a CAD STEP model
  2. Shift part so Top surface is at Z=0 (G-code standard)
  3. Create FreeCAD CAM Job with exact Stock dimensions
  4. Attach standard ToolController (10mm Endmill)
  5. Identify pocket floor faces and generate native FreeCAD PocketShape operations
  6. Recompute and export clean LinuxCNC machine code (.ngc)
"""

import sys
import os
import json
import math

# Clean sys.path to ensure compatibility
curr_py = f"python3.{sys.version_info.minor}"
sys.path = [p for p in sys.path if not any(f"python3.{m}" in p for m in range(7, 15) if m != sys.version_info.minor)]

import FreeCAD
import Part
import Path
import Path.Main.Job as PathJob
from Path.Tool.toolbit import ToolBit
import Path.Tool.Controller as PathToolController
import Path.Op.PocketShape as PathPocketShape

def build_freecad_cam_baseline(step_path, output_ngc, tool_dia=10.0, stepdown=4.0, feed_horiz=1400.0, feed_vert=560.0, spindle_rpm=8000.0):
    if not os.path.exists(step_path):
        raise FileNotFoundError(f"STEP file not found: {step_path}")

    doc = FreeCAD.newDocument("BenchmarkBaselineJob")
    
    # 1. Load CAD Part
    shape = Part.Shape()
    shape.read(step_path)
    part_obj = doc.addObject("Part::Feature", "Nominal_CAD_Part")
    part_obj.Shape = shape

    bb = shape.BoundBox
    top_z = bb.ZMax
    total_height = bb.ZLength

    # Align part so Top is at Z=0 (G-code convention: Z <= 0 into workpiece)
    part_obj.Placement.Base = FreeCAD.Vector(0.0, 0.0, -top_z)
    doc.recompute()

    # 2. Create CAM Job
    job = PathJob.Create("Job", [part_obj])
    if hasattr(job, "Stock") and job.Stock:
        job.Stock.ExtXneg = 0.0
        job.Stock.ExtXpos = 0.0
        job.Stock.ExtYneg = 0.0
        job.Stock.ExtYpos = 0.0
        job.Stock.ExtZneg = 0.0
        job.Stock.ExtZpos = 0.0

    # 3. Add Tool Controller (Endmill)
    tool_attrs = {
        "name": f"Endmill_{int(tool_dia)}mm",
        "shape": "endmill.fcstd",
        "parameter": {"Diameter": float(tool_dia), "CuttingEdgeHeight": 30.0, "Length": 50.0},
        "attribute": {}
    }
    toolbit = ToolBit.from_dict(tool_attrs)
    t_obj = toolbit.attach_to_doc(doc=doc)
    tc = PathToolController.Create(f"TC_{int(tool_dia)}mm", t_obj)
    tc.ToolNumber = 1
    tc.HorizFeed = float(feed_horiz)
    tc.VertFeed = float(feed_vert)
    tc.SpindleSpeed = float(spindle_rpm)
    job.Tools.Group = [tc]

    # 4. Identify Pocket Floors
    # A pocket floor is a planar horizontal face with normal pointing +Z, strictly below Top and above Bottom
    # Area threshold: Pockets must be large enough for the tool (area > tool circle area * 0.8)
    min_pocket_area = max(50.0, math.pi * (tool_dia / 2.0) ** 2 * 0.5)
    
    pocket_faces = []
    for i, face in enumerate(shape.Faces):
        face_name = f"Face{i+1}"
        f_bb = face.BoundBox
        surf_str = str(face.Surface)

        # Planar horizontal face: ZMin ~= ZMax
        if abs(f_bb.ZMax - f_bb.ZMin) < 0.05 and "Plane" in surf_str:
            try:
                norm = face.normalAt(0, 0)
                if norm.z > 0.85:
                    depth = round(top_z - f_bb.ZMax, 3)
                    # Depth must be between 0.5mm and total_height (exclude top face and through-cut bottom)
                    if 0.5 <= depth < (total_height - 0.05) and face.Area >= min_pocket_area:
                        pocket_faces.append((face_name, depth, face.Area))
            except Exception:
                pass

    # Sort shallow-to-deep
    pocket_faces.sort(key=lambda x: (x[1], -x[2]))

    operations = []
    # If part has specific pocket faces, create pocket operations
    for op_idx, (face_name, depth, area) in enumerate(pocket_faces, 1):
        try:
            pocket_op = PathPocketShape.Create(f"Pocket_{op_idx}_D{int(depth*10)}")
            pocket_op.Base = [(part_obj, [face_name])]
            pocket_op.ToolController = tc
            pocket_op.StepDown = float(stepdown)
            pocket_op.CutMode = "Climb"
            operations.append(pocket_op)
        except Exception as e:
            print(f"[!] Warning: Failed to create pocket on {face_name}: {e}")

    job.Operations.Group = operations
    doc.recompute()

    # 5. Synthesize G-Code
    gcode_lines = [
        "(Exported by FreeCAD CAM Headless Baseline Generator)",
        "(Machine: LinuxCNC Standard Dialect)",
        f"(Part: {os.path.basename(step_path)})",
        f"(Tool: Dia {tool_dia}mm, RPM: {spindle_rpm}, Feed: {feed_horiz}mm/min)",
        "G17 G54 G40 G49 G80 G90",
        "G21",
        f"G00 Z15.000",
        f"T1 M06",
        f"S{int(spindle_rpm)} M03",
        ""
    ]

    total_ops_gcode = 0
    for op in operations:
        if hasattr(op, "Path") and op.Path and len(op.Path.Commands) > 0:
            op_code = op.Path.toGCode()
            gcode_lines.append(f"(--- Operation: {op.Label} ---)")
            gcode_lines.append(op_code.strip())
            gcode_lines.append("")
            total_ops_gcode += 1

    gcode_lines.extend([
        "(--- Program End ---)",
        "G00 Z25.000",
        "M05",
        "M02"
    ])

    final_gcode = "\n".join(gcode_lines) + "\n"

    os.makedirs(os.path.dirname(os.path.abspath(output_ngc)), exist_ok=True)
    with open(output_ngc, "w") as f:
        f.write(final_gcode)

    summary = {
        "status": "SUCCESS",
        "step_path": step_path,
        "output_ngc": output_ngc,
        "operations_created": len(operations),
        "operations_with_toolpath": total_ops_gcode,
        "total_gcode_lines": len(final_gcode.splitlines())
    }
    sys.__stdout__.write(f"__BASELINE_RESULT__{json.dumps(summary)}\n")
    sys.__stdout__.flush()
    return summary


try:
    script_idx = -1
    for i, arg in enumerate(sys.argv):
        if "_freecad_worker.py" in arg:
            script_idx = i
            break
    worker_args = sys.argv[script_idx + 1:] if script_idx >= 0 else []
    if worker_args and worker_args[0] in ("--", "--pass"):
        worker_args = worker_args[1:]

    if len(worker_args) < 2:
        print("Usage: freecadcmd _freecad_worker.py --pass <step_file> <output_ngc> [tool_dia] [stepdown]")
        sys.exit(1)

    step_in = worker_args[0]
    ngc_out = worker_args[1]
    t_dia = float(worker_args[2]) if len(worker_args) > 2 else 10.0
    s_down = float(worker_args[3]) if len(worker_args) > 3 else 4.0

    build_freecad_cam_baseline(step_in, ngc_out, tool_dia=t_dia, stepdown=s_down)
    sys.exit(0)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
