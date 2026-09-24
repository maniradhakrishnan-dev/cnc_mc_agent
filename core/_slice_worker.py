#!/usr/bin/env python3
"""
FreeCAD B-Rep Cross-Section Slicing Worker
Computes exact 2D cross-sections of negative space (material to be removed)
at specified Z-levels from the nominal CAD solid.
"""

import sys
import os
import json
import math
import FreeCAD
import Part

def slice_negative_space(step_file, output_json, z_levels_spec=None):
    if not os.path.exists(step_file):
        raise FileNotFoundError(f"STEP file not found: {step_file}")

    shape = Part.Shape()
    shape.read(step_file)
    bb = shape.BoundBox

    stock = Part.makeBox(
        bb.XLength, bb.YLength, bb.ZLength,
        FreeCAD.Vector(bb.XMin, bb.YMin, bb.ZMin)
    )

    # Boolean cut: stock - CAD solid = negative space (material to remove)
    negative = stock.cut(shape)

    top_z = bb.ZMax
    bottom_z = bb.ZMin
    total_depth = bb.ZLength

    # Determine requested Z levels (in G-code coordinates, where stock top = 0.0, Z <= 0.0)
    z_gcode_list = []
    if z_levels_spec:
        if isinstance(z_levels_spec, str):
            if os.path.isfile(z_levels_spec):
                with open(z_levels_spec, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        z_gcode_list = [float(z) for z in data]
                    elif isinstance(data, dict) and "z_levels" in data:
                        z_gcode_list = [float(z) for z in data["z_levels"]]
            else:
                try:
                    parsed = json.loads(z_levels_spec)
                    if isinstance(parsed, list):
                        z_gcode_list = [float(z) for z in parsed]
                    else:
                        stepdown = float(parsed)
                        cur = -stepdown
                        while cur >= -total_depth:
                            z_gcode_list.append(round(cur, 3))
                            cur -= stepdown
                except (ValueError, json.JSONDecodeError):
                    try:
                        stepdown = float(z_levels_spec)
                        cur = -stepdown
                        while cur >= -total_depth:
                            z_gcode_list.append(round(cur, 3))
                            cur -= stepdown
                    except ValueError:
                        pass
        elif isinstance(z_levels_spec, (int, float)):
            stepdown = float(z_levels_spec)
            cur = -stepdown
            while cur >= -total_depth:
                z_gcode_list.append(round(cur, 3))
                cur -= stepdown
        elif isinstance(z_levels_spec, list):
            z_gcode_list = [float(z) for z in z_levels_spec]

    if not z_gcode_list:
        # Default: 1.0mm stepdown grid
        stepdown = 1.0
        cur = -stepdown
        while cur >= -total_depth:
            z_gcode_list.append(round(cur, 3))
            cur -= stepdown

    # Sort descending (shallowest / closest to 0 first)
    z_gcode_list = sorted(list(set(round(z, 3) for z in z_gcode_list if z <= 0.0)), reverse=True)

    slices_dict = {}
    for z_g in z_gcode_list:
        z_cad = top_z + z_g
        # Clamp within valid stock bounds
        target_z = max(bottom_z + 0.001, min(top_z - 0.001, z_cad))

        # 1. First attempt exact Z slicing
        slice_z_cad = target_z
        wires = negative.slice(FreeCAD.Vector(0, 0, 1), float(slice_z_cad))

        # 2. If on exact boundary with zero wires, retry with tiny upward epsilon (+0.01mm)
        if not wires and (target_z + 0.01) < (top_z - 0.001):
            slice_z_cad = target_z + 0.01
            wires = negative.slice(FreeCAD.Vector(0, 0, 1), float(slice_z_cad))

        # 3. If still no wires, retry with tiny downward epsilon (-0.01mm)
        if not wires and (target_z - 0.01) > (bottom_z + 0.001):
            slice_z_cad = target_z - 0.01
            wires = negative.slice(FreeCAD.Vector(0, 0, 1), float(slice_z_cad))

        wire_data = []
        for w in wires:
            # Discretize wire to fine XY point sequence (deflection 0.05mm gives sub-0.1mm chord accuracy)
            pts = w.discretize(Deflection=0.05)
            if len(pts) < 3:
                continue
            wb = w.BoundBox
            pts_xy = [[round(p.x, 3), round(p.y, 3)] for p in pts]
            wire_data.append({
                "points": pts_xy,
                "is_closed": bool(w.isClosed()),
                "bbox": [round(wb.XMin, 3), round(wb.YMin, 3), round(wb.XMax, 3), round(wb.YMax, 3)],
                "point_count": len(pts_xy)
            })

        slices_dict[f"{z_g:.3f}"] = {
            "z_gcode": z_g,
            "z_cad": round(z_cad, 3),
            "slice_z_cad": round(slice_z_cad, 3),
            "wires": wire_data,
            "wire_count": len(wire_data)
        }

    result = {
        "source_cad_file": os.path.basename(step_file),
        "stock_bounds": {
            "min": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)],
            "max": [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)],
            "dimensions": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
        },
        "cad_top_z": round(top_z, 3),
        "cad_bottom_z": round(bottom_z, 3),
        "total_depth_mm": round(total_depth, 3),
        "total_slices": len(slices_dict),
        "z_slices": slices_dict
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_json)), exist_ok=True)
    with open(output_json, "w") as f:
        json.dump(result, f, indent=2)

    print("__SLICING_SUCCESS__")
    return result

try:
    script_idx = -1
    for i, arg in enumerate(sys.argv):
        if "_slice_worker.py" in arg:
            script_idx = i
            break
    worker_args = sys.argv[script_idx + 1:] if script_idx >= 0 else []
    if worker_args and worker_args[0] in ("--", "--pass"):
        worker_args = worker_args[1:]

    _this_dir = os.path.dirname(os.path.abspath(__file__))
    step_arg = worker_args[0] if len(worker_args) > 0 else (sys.argv[-2] if len(sys.argv) >= 3 else os.path.join(_this_dir, "step", "machining_block_04.step"))
    out_arg = worker_args[1] if len(worker_args) > 1 else (sys.argv[-1] if len(sys.argv) >= 3 else os.path.join(_this_dir, "slices.json"))
    z_arg = worker_args[2] if len(worker_args) > 2 else None

    slice_negative_space(step_arg, out_arg, z_arg)
    sys.exit(0)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
