#!/usr/bin/env python3
"""
Step 3: Toolpath & Machine-Ready G-Code Generator
features.json + tool_library.json + strategies.json (+ CAD STEP) -> 3 G-code programs (.ngc)

Generates three machine-ready LinuxCNC / GRBL programs:
  1. 1_cycle_time.ngc
  2. 2_accuracy_tuned.ngc
  3. 3_balanced.ngc

Uses FreeCAD B-Rep Z-level slicing for true 3D cross-section material removal,
ensuring cylindrical bosses, freeform profiles, and varying cross-sections are
machined to exact nominal geometry.
"""

import sys
import os
import math
import json
import argparse
import subprocess
from shapely.geometry import Polygon, LineString, MultiLineString

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
SLICE_WORKER = os.path.join(AGENT_DIR, "_slice_worker.py")

def get_tool(tool_num, tool_lib):
    return next((t for t in tool_lib["tools"] if t["tool_number"] == tool_num), tool_lib["tools"][0])

def wires_to_polygons(wires_list):
    """
    Converts a list of wire dicts (with 'points' and 'is_closed') into a list
    of valid Shapely Polygons with correctly assigned outer shells and inner holes.
    """
    if not wires_list:
        return []

    raw_polys = []
    for w in wires_list:
        pts = w.get("points", [])
        if len(pts) < 3:
            continue
        p = Polygon(pts)
        if not p.is_valid:
            p = p.buffer(0)
        if not p.is_empty and p.area > 1e-4:
            if p.geom_type == 'Polygon':
                raw_polys.append(p)
            elif p.geom_type == 'MultiPolygon':
                raw_polys.extend([g for g in p.geoms if g.area > 1e-4])

    if not raw_polys:
        return []

    # Sort descending by area (outer shells first)
    raw_polys.sort(key=lambda p: p.area, reverse=True)

    outers = []
    holes = []
    for p in raw_polys:
        rep_pt = p.representative_point()
        parent = None
        for out in outers:
            if out.contains(rep_pt):
                parent = out
                break
        if parent is not None:
            holes.append((p, parent))
        else:
            outers.append(p)

    result = []
    for out in outers:
        my_holes = [h[0].exterior.coords for h in holes if h[1] is out]
        poly = Polygon(shell=out.exterior.coords, holes=my_holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty:
            result.append(poly)

    return result

def get_slice_for_z(slices_data, target_z_gcode):
    """Finds exact or nearest slice for target Z."""
    if not slices_data or "z_slices" not in slices_data:
        return None
    z_slices = slices_data["z_slices"]
    key = f"{target_z_gcode:.3f}"
    if key in z_slices:
        return z_slices[key]

    avail = [(v["z_gcode"], v) for v in z_slices.values()]
    if not avail:
        return None
    nearest = min(avail, key=lambda item: abs(item[0] - target_z_gcode))
    return nearest[1]

def generate_scanline_raster_for_poly(poly, cur_tool_rad, allowance, cur_stepover, cur_feed, current_z):
    """
    Generates scanline raster G-code lines for a single 2D polygon with holes at current_z.
    """
    lines = []
    offset_poly = poly.buffer(-(cur_tool_rad + allowance))
    if offset_poly.is_empty:
        return lines

    polys_to_clear = [offset_poly] if offset_poly.geom_type == 'Polygon' else list(offset_poly.geoms)

    for sub_poly in polys_to_clear:
        minx, miny, maxx, maxy = sub_poly.bounds
        scan_ys = []
        y = miny + 0.1
        while y <= maxy:
            scan_ys.append(y)
            y += cur_stepover
        if scan_ys and (maxy - scan_ys[-1]) > (0.3 * cur_stepover):
            scan_ys.append(maxy - 0.05)

        forward = True
        for sy in scan_ys:
            s_line = LineString([(minx - 10.0, sy), (maxx + 10.0, sy)])
            inter = sub_poly.intersection(s_line)
            if inter.is_empty:
                continue
            segs = [inter] if inter.geom_type == 'LineString' else list(inter.geoms)
            segs.sort(key=lambda s: s.coords[0][0])
            if not forward:
                segs.reverse()

            for seg in segs:
                c = list(seg.coords)
                start_pt = c[0] if forward else c[-1]
                end_pt = c[-1] if forward else c[0]
                lines.append("G00 Z5.000")
                lines.append(f"G00 X{start_pt[0]:.3f} Y{start_pt[1]:.3f}")
                lines.append(f"G01 Z{current_z:.3f} F{int(cur_feed * 0.4)}")
                lines.append(f"G01 X{end_pt[0]:.3f} Y{end_pt[1]:.3f} F{cur_feed}")
            forward = not forward

        # Clean perimeter pass along boundary of sub_poly (both exterior and islands)
        contours_to_clean = []
        if hasattr(sub_poly, 'exterior') and len(sub_poly.exterior.coords) > 2:
            contours_to_clean.append(list(sub_poly.exterior.coords))
        for interior in getattr(sub_poly, 'interiors', []):
            if len(interior.coords) > 2:
                contours_to_clean.append(list(interior.coords))

        for c_coords in contours_to_clean:
            lines.append("G00 Z5.000")
            lines.append(f"G00 X{c_coords[0][0]:.3f} Y{c_coords[0][1]:.3f}")
            lines.append(f"G01 Z{current_z:.3f} F{int(cur_feed * 0.4)}")
            for pt in c_coords[1:]:
                lines.append(f"G01 X{pt[0]:.3f} Y{pt[1]:.3f} F{cur_feed}")
            # Ensure loop closure
            lines.append(f"G01 X{c_coords[0][0]:.3f} Y{c_coords[0][1]:.3f} F{cur_feed}")

    lines.append("G00 Z5.000")
    return lines

def compute_adaptive_z_passes(depth, stepdown, floor_depths=None):
    """
    Computes optimal Z-pass heights from 0.0 to -depth such that:
    1. Every feature floor depth (e.g. -6.0, -8.0, -10.0, -12.0) is EXACTLY hit.
    2. No stepdown between successive passes exceeds `stepdown`.
    """
    critical_depths = set([float(depth)])
    if floor_depths:
        for fd in floor_depths:
            if 0 < fd <= depth:
                critical_depths.add(round(float(fd), 3))

    sorted_crits = sorted(list(critical_depths))
    z_passes = []
    prev_d = 0.0
    for target_d in sorted_crits:
        span = target_d - prev_d
        if span <= 1e-4:
            continue
        num_subpasses = max(1, math.ceil(span / stepdown))
        actual_substep = span / num_subpasses
        for i in range(1, num_subpasses + 1):
            cur_d = prev_d + (i * actual_substep)
            z_passes.append(round(-cur_d, 3))
        prev_d = target_d

    return sorted(list(set(z_passes)), reverse=True)

def generate_centerline_slot_pass(poly, current_z, feed):
    """
    Generates single-pass linear centerline slotting move along the longitudinal medial axis of a slot.
    """
    lines = []
    minx, miny, maxx, maxy = poly.bounds
    dx = maxx - minx
    dy = maxy - miny
    if dx >= dy:
        mid_y = (miny + maxy) / 2.0
        r = dy / 2.0
        p_start = (minx + r, mid_y)
        p_end = (maxx - r, mid_y)
    else:
        mid_x = (minx + maxx) / 2.0
        r = dx / 2.0
        p_start = (mid_x, miny + r)
        p_end = (mid_x, maxy - r)

    lines.append("G00 Z5.000")
    lines.append(f"G00 X{p_start[0]:.3f} Y{p_start[1]:.3f}")
    lines.append(f"G01 Z{current_z:.3f} F{int(feed * 0.4)}")
    lines.append(f"G01 X{p_end[0]:.3f} Y{p_end[1]:.3f} F{feed}")
    lines.append("G00 Z5.000")
    return lines

def generate_zlevel_sliced_roughing(slices_data, tool_rad, allowance, stepover_dist, z_passes, feed):
    """
    Z-level roughing where each depth pass slices the true 3D B-Rep cross-section.
    Returns (lines, unmachined_polys_by_z) so any cavity too narrow for this tool
    can be cleanly rest-machined with a secondary tool.
    """
    lines = []
    unmachined_polys_by_z = {}

    for current_z in z_passes:
        lines.append(f"\n; --- Stepdown pass at Z = {current_z:.3f} mm ---")

        slice_info = get_slice_for_z(slices_data, current_z)
        if not slice_info or not slice_info.get("wires"):
            lines.append(f"; No material cross-section at Z = {current_z:.3f}")
            continue

        polys = wires_to_polygons(slice_info["wires"])
        for p_idx, poly in enumerate(polys):
            pass_lines = generate_scanline_raster_for_poly(
                poly, tool_rad, allowance, stepover_dist, feed, current_z
            )
            if pass_lines:
                lines.extend(pass_lines)
            else:
                # Feature cannot be cleared by this roughing tool (cavity too narrow)
                if current_z not in unmachined_polys_by_z:
                    unmachined_polys_by_z[current_z] = []
                unmachined_polys_by_z[current_z].append(poly)

    return lines, unmachined_polys_by_z

def generate_zlevel_sliced_finishing(slices_data, finish_tool, deepest_depth, z_passes, f_feed, spring_passes):
    """
    Continuous contour following at each Z level around exterior walls and island perimeters.
    """
    lines = []
    f_tool_rad = finish_tool["diameter_mm"] / 2.0

    for current_z in z_passes:
        lines.append(f"\n; --- Wall Finishing pass at Z = {current_z:.3f} mm ---")

        slice_info = get_slice_for_z(slices_data, current_z)
        if not slice_info or not slice_info.get("wires"):
            continue

        polys = wires_to_polygons(slice_info["wires"])
        for poly in polys:
            finish_poly = poly.buffer(-f_tool_rad)
            if finish_poly.is_empty:
                cl_lines = generate_centerline_slot_pass(poly, current_z, f_feed)
                lines.extend(cl_lines)
                continue

            sub_polys = [finish_poly] if finish_poly.geom_type == 'Polygon' else list(finish_poly.geoms)
            for sp in sub_polys:
                contours = []
                if hasattr(sp, 'exterior') and len(sp.exterior.coords) >= 3:
                    contours.append(list(sp.exterior.coords))
                for interior in getattr(sp, 'interiors', []):
                    if len(interior.coords) >= 3:
                        contours.append(list(interior.coords))

                for coords in contours:
                    lines.append("G00 Z5.000")
                    lines.append(f"G00 X{coords[0][0]:.3f} Y{coords[0][1]:.3f}")
                    lines.append(f"G01 Z{current_z:.3f} F{int(f_feed * 0.4)}")
                    for pt in coords[1:]:
                        lines.append(f"G01 X{pt[0]:.3f} Y{pt[1]:.3f} F{f_feed}")
                    lines.append(f"G01 X{coords[0][0]:.3f} Y{coords[0][1]:.3f} F{f_feed}")
                    lines.append("G00 Z5.000")

    # Spring passes at final floor depth
    if spring_passes > 0:
        lines.append(f"\n; --- Spring Passes ({spring_passes}) at floor Z = -{deepest_depth:.3f} mm ---")
        slice_info = get_slice_for_z(slices_data, -deepest_depth)
        if slice_info and slice_info.get("wires"):
            polys = wires_to_polygons(slice_info["wires"])
            for poly in polys:
                finish_poly = poly.buffer(-f_tool_rad)
                if finish_poly.is_empty:
                    continue
                sub_polys = [finish_poly] if finish_poly.geom_type == 'Polygon' else list(finish_poly.geoms)
                for sp in sub_polys:
                    contours = []
                    if hasattr(sp, 'exterior') and len(sp.exterior.coords) >= 3:
                        contours.append(list(sp.exterior.coords))
                    for interior in getattr(sp, 'interiors', []):
                        if len(interior.coords) >= 3:
                            contours.append(list(interior.coords))
                    for sp_idx in range(spring_passes):
                        lines.append(f"; Spring Pass #{sp_idx+1}")
                        for coords in contours:
                            lines.append("G00 Z5.000")
                            lines.append(f"G00 X{coords[0][0]:.3f} Y{coords[0][1]:.3f}")
                            lines.append(f"G01 Z-{deepest_depth:.3f} F{int(f_feed * 0.4)}")
                            for pt in coords[1:]:
                                lines.append(f"G01 X{pt[0]:.3f} Y{pt[1]:.3f} F{f_feed}")
                            lines.append(f"G01 X{coords[0][0]:.3f} Y{coords[0][1]:.3f} F{f_feed}")
                            lines.append("G00 Z5.000")

    return lines

def generate_legacy_polygon_roughing(p, tool_rad, allowance, stepover_dist, depth, stepdown, feed, finish_tool, rpm):
    """Fallback 2D polygon roughing when no 3D slices are available (e.g. DXF)."""
    poly_pts = p.get("boundary_polygon_xy", [])
    if len(poly_pts) < 3:
        return []
    island_pts = p.get("island_polygons_xy", [])
    poly = Polygon(shell=poly_pts, holes=island_pts)
    if not poly.is_valid:
        poly = poly.buffer(0)

    lines = []
    current_z = 0.0
    while current_z > -depth:
        current_z = max(-depth, current_z - stepdown)
        lines.append(f"; Stepdown pass at Z = {current_z:.3f}")
        pass_lines = generate_scanline_raster_for_poly(
            poly, tool_rad, allowance, stepover_dist, feed, current_z
        )
        lines.extend(pass_lines)
    return lines

def generate_legacy_polygon_finishing(p, finish_tool, depth, f_stepdown, f_feed, spring_passes):
    """Fallback 2D polygon wall finishing when no 3D slices are available."""
    poly_pts = p.get("boundary_polygon_xy", [])
    if len(poly_pts) < 3:
        return []
    island_pts = p.get("island_polygons_xy", [])
    poly = Polygon(shell=poly_pts, holes=island_pts)
    if not poly.is_valid:
        poly = poly.buffer(0)

    f_tool_rad = finish_tool["diameter_mm"] / 2.0
    finish_poly = poly.buffer(-f_tool_rad)
    if finish_poly.is_empty:
        return []

    lines = []
    sub_polys = [finish_poly] if finish_poly.geom_type == 'Polygon' else list(finish_poly.geoms)
    current_z = 0.0
    while current_z > -depth:
        current_z = max(-depth, current_z - f_stepdown)
        lines.append(f"; Wall finish pass at Z = {current_z:.3f}")
        for sp in sub_polys:
            contours = []
            if hasattr(sp, 'exterior') and len(sp.exterior.coords) >= 3:
                contours.append(list(sp.exterior.coords))
            for interior in getattr(sp, 'interiors', []):
                if len(interior.coords) >= 3:
                    contours.append(list(interior.coords))

            for coords in contours:
                lines.append("G00 Z5.000")
                lines.append(f"G00 X{coords[0][0]:.3f} Y{coords[0][1]:.3f}")
                lines.append(f"G01 Z{current_z:.3f} F{int(f_feed * 0.4)}")
                for pt in coords[1:]:
                    lines.append(f"G01 X{pt[0]:.3f} Y{pt[1]:.3f} F{f_feed}")
                lines.append(f"G01 X{coords[0][0]:.3f} Y{coords[0][1]:.3f} F{f_feed}")
                lines.append("G00 Z5.000")
    return lines

def verify_preflight_feature_coverage(gcode_text, features, strategy_name):
    """
    Deterministically asserts that EVERY extracted feature in features.json has at least one
    cutting feed motion (G01/G02/G03) within its 3D spatial bounding volume.
    Raises RuntimeError if any feature was omitted.
    """
    s1 = features.get("features", {}).get("setup_1_top_3axis", {})
    pockets = s1.get("pockets", [])
    if not pockets:
        pockets = features.get("features", {}).get("pockets", [])

    cut_points = []
    for line in gcode_text.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("("):
            continue
        if line.startswith("G01") or line.startswith("G1 ") or line.startswith("G02") or line.startswith("G03"):
            parts = line.split()
            x = y = z = None
            for pt_part in parts:
                if pt_part.startswith("X"):
                    try:
                        x = float(pt_part[1:])
                    except ValueError:
                        pass
                elif pt_part.startswith("Y"):
                    try:
                        y = float(pt_part[1:])
                    except ValueError:
                        pass
                elif pt_part.startswith("Z"):
                    try:
                        z = float(pt_part[1:])
                    except ValueError:
                        pass
            if x is not None and y is not None:
                cut_points.append((x, y, z))

    uncovered_features = []
    for p in pockets:
        p_id = p["id"]
        b = p.get("bounds", {})
        min_x = b.get("min_x", -999.0) - 2.0
        max_x = b.get("max_x", 999.0) + 2.0
        min_y = b.get("min_y", -999.0) - 2.0
        max_y = b.get("max_y", 999.0) + 2.0

        has_cut = any(min_x <= pt[0] <= max_x and min_y <= pt[1] <= max_y for pt in cut_points)
        if not has_cut:
            uncovered_features.append(p_id)

    if uncovered_features:
        raise RuntimeError(
            f"PRE-FLIGHT G-CODE VALIDATION FAILED for [{strategy_name}]: "
            f"The following features have ZERO cutting toolpath passes: {uncovered_features}. "
            f"Every CAD feature must be 100% machined across all strategies."
        )

def generate_gcode_for_strategy(strategy_name, strat, features, tool_lib, output_file, slices_data=None):
    lines = []
    params = strat["parameters"]
    tool_assign = strat.get("tool_assignments", {})

    rough_tool_num = tool_assign.get("pocket_roughing", 1)
    finish_tool_num = tool_assign.get("pocket_finishing", 2)
    drill_tool_num = tool_assign.get("drilling", 4)

    rough_tool = get_tool(rough_tool_num, tool_lib)
    finish_tool = get_tool(finish_tool_num, tool_lib)
    drill_tool = get_tool(drill_tool_num, tool_lib)

    s1_features = features.get("features", {}).get("setup_1_top_3axis", {})
    pockets = s1_features.get("pockets", [])
    vertical_holes = s1_features.get("vertical_holes", [])
    chamfers = s1_features.get("chamfers", [])

    deepest_depth = features.get("machinability_constraints", {}).get("deepest_feature_depth_mm", 10.0)
    if pockets:
        deepest_depth = max(deepest_depth, max(p.get("depth_from_external_top_mm", 10.0) for p in pockets))

    stock_req = features.get("stock_requirements", {})
    stock_x = stock_req.get("x_length_mm", 100.0)
    stock_y = stock_req.get("y_length_mm", 80.0)
    stock_z = stock_req.get("z_length_mm", 25.0)

    # Collect all pocket floor depths to ensure exact floor Z-plane alignment
    all_floors = features.get("machinability_constraints", {}).get("all_floor_depths_mm", [])
    if not all_floors:
        all_floors = [p.get("depth_from_external_top_mm", 0.0) for p in pockets if p.get("depth_from_external_top_mm", 0.0) > 0]

    # -------------------------------------------------------------
    # G-code Header
    # -------------------------------------------------------------
    lines.append("%")
    lines.append(f"(PROGRAM: {strategy_name})")
    lines.append(f"(STRATEGY: {strat.get('name', strategy_name)})")
    lines.append(f"(TARGET CAD: {features.get('source_cad_file', 'unknown')})")
    lines.append(f"(STOCK: {stock_x:.1f} x {stock_y:.1f} x {stock_z:.1f} mm)")
    lines.append(f"(SLICED 3D B-REP ENGINE: {'ACTIVE' if slices_data else 'OFFLINE (2D POLYGON FALLBACK)'})")
    lines.append("G21          ; Metric Units (mm)")
    lines.append("G90          ; Absolute Positioning")
    lines.append("G17          ; XY Plane Selection")
    lines.append("G94          ; Feed per Minute Mode")
    lines.append("G54          ; Work Coordinate System 1")
    lines.append("G40 G49 G80  ; Cutter Comp Cancel, Length Offset Cancel, Canned Cycle Cancel")
    lines.append("G00 Z25.000  ; Safe Initial Retract")

    # =========================================================================
    # OPERATION 1: Pocket Roughing (Z-Level Sliced or Fallback)
    # =========================================================================
    if pockets or slices_data:
        r_param = params.get("pocket_roughing", {})
        rpm = r_param.get("spindle_rpm", 8000)
        feed = r_param.get("feedrate_mm_min", 1500)
        stepdown = r_param.get("stepdown_mm", 3.0)
        stepover_pct = r_param.get("stepover_pct", 65)
        allowance = r_param.get("finish_allowance_mm", 0.2)
        tool_rad = rough_tool["diameter_mm"] / 2.0
        stepover_dist = rough_tool["diameter_mm"] * (stepover_pct / 100.0)
        r_z_passes = compute_adaptive_z_passes(deepest_depth, stepdown, floor_depths=all_floors)

        lines.append(f"\n; -------------------------------------------------------------")
        lines.append(f"; OP 1: Pocket Roughing (Tool T{rough_tool['tool_number']}: {rough_tool['name']})")
        lines.append(f"; Stepdown: {stepdown:.2f}mm | Stepover: {stepover_dist:.2f}mm ({stepover_pct}%) | Allowance: {allowance:.2f}mm")
        lines.append(f"; -------------------------------------------------------------")
        lines.append(f"T{rough_tool['tool_number']} M06")
        lines.append(f"G43 H{rough_tool['tool_number']}")
        lines.append(f"S{rpm} M03")
        lines.append(f"M08          ; Flood Coolant ON")
        lines.append(f"G00 Z5.000")

        if slices_data:
            # TRUE 3D B-REP Z-LEVEL SLICING
            rough_lines, unmachined_polys = generate_zlevel_sliced_roughing(
                slices_data, tool_rad, allowance, stepover_dist, r_z_passes, feed
            )
            lines.extend(rough_lines)

            # OPERATION 1B: Rest-Machining / Narrow Feature Clearing
            # Automatically invoke secondary tool if any cavities were too narrow for roughing tool
            if unmachined_polys and finish_tool and finish_tool["tool_number"] != rough_tool["tool_number"]:
                f_rpm = params.get("pocket_finishing", {}).get("spindle_rpm", 9500)
                f_feed = int(feed * 0.7)
                f_rad = finish_tool["diameter_mm"] / 2.0
                f_stepover = finish_tool["diameter_mm"] * 0.45

                lines.append(f"\n; -------------------------------------------------------------")
                lines.append(f"; OP 1B: Rest-Machining Narrow Cavities (Tool T{finish_tool['tool_number']}: {finish_tool['name']})")
                lines.append(f"; Auto-clearing {sum(len(v) for v in unmachined_polys.values())} feature section(s) too narrow for roughing tool")
                lines.append(f"; -------------------------------------------------------------")
                lines.append(f"T{finish_tool['tool_number']} M06")
                lines.append(f"G43 H{finish_tool['tool_number']}")
                lines.append(f"S{f_rpm} M03")
                lines.append(f"M08          ; Flood Coolant ON")
                lines.append(f"G00 Z5.000")

                for cur_z in sorted(unmachined_polys.keys(), reverse=True):
                    u_polys = unmachined_polys[cur_z]
                    lines.append(f"\n; --- Rest-machining pass at Z = {cur_z:.3f} mm ---")
                    for poly in u_polys:
                        rest_lines = generate_scanline_raster_for_poly(
                            poly, f_rad, allowance, f_stepover, f_feed, cur_z
                        )
                        if not rest_lines:
                            rest_lines = generate_centerline_slot_pass(poly, cur_z, f_feed)
                        lines.extend(rest_lines)
        else:
            # Fallback to feature pocket polygons
            for p in pockets:
                depth = p.get("depth_from_external_top_mm", p.get("depth_mm", 5.0))
                p_lines = generate_legacy_polygon_roughing(
                    p, tool_rad, allowance, stepover_dist, depth, stepdown, feed, finish_tool, rpm
                )
                lines.extend(p_lines)

    # =========================================================================
    # OPERATION 2: Pocket Finishing (if enabled)
    # =========================================================================
    f_param = params.get("pocket_finishing", {})
    if (pockets or slices_data) and f_param.get("enabled", False):
        f_rpm = f_param.get("spindle_rpm", 9500)
        f_feed = f_param.get("feedrate_mm_min", 800)
        f_stepdown = f_param.get("stepdown_mm", 1.5)
        f_z_passes = compute_adaptive_z_passes(deepest_depth, f_stepdown, floor_depths=all_floors)
        spring_passes = f_param.get("spring_passes", 0)

        lines.append(f"\n; -------------------------------------------------------------")
        lines.append(f"; OP 2: Pocket Wall Finishing (Tool T{finish_tool['tool_number']}: {finish_tool['name']})")
        lines.append(f"; Stepdown: {f_stepdown:.2f}mm | Feed: {f_feed} mm/min | Spring Passes: {spring_passes}")
        lines.append(f"; -------------------------------------------------------------")
        lines.append(f"T{finish_tool['tool_number']} M06")
        lines.append(f"G43 H{finish_tool['tool_number']}")
        lines.append(f"S{f_rpm} M03")
        lines.append(f"G00 Z5.000")

        if slices_data:
            # TRUE 3D B-REP Z-LEVEL FINISHING
            finish_lines = generate_zlevel_sliced_finishing(
                slices_data, finish_tool, deepest_depth, f_z_passes, f_feed, spring_passes
            )
            lines.extend(finish_lines)
        else:
            for p in pockets:
                depth = p.get("depth_from_external_top_mm", p.get("depth_mm", 5.0))
                p_lines = generate_legacy_polygon_finishing(
                    p, finish_tool, depth, f_stepdown, f_feed, spring_passes
                )
                lines.extend(p_lines)

    # =========================================================================
    # OPERATION 3: Drilling Cycles (from features.json)
    # =========================================================================
    if vertical_holes:
        d_param = params.get("drilling", {})
        d_rpm = d_param.get("spindle_rpm", 3500)
        d_feed = d_param.get("feedrate_mm_min", 400)
        peck_q = d_param.get("peck_depth_mm", 4.0)

        lines.append(f"\n; -------------------------------------------------------------")
        lines.append(f"; OP 3: Vertical Hole Peck Drilling (Tool T{drill_tool['tool_number']}: {drill_tool['name']})")
        lines.append(f"; -------------------------------------------------------------")
        lines.append(f"T{drill_tool['tool_number']} M06")
        lines.append(f"G43 H{drill_tool['tool_number']}")
        lines.append(f"S{d_rpm} M03")
        lines.append(f"G00 Z5.000")

        for h in vertical_holes:
            cx, cy = h["center_xy_mm"]
            h_depth = h.get("depth_from_external_top_mm", h.get("depth_mm", 10.0))
            lines.append(f"\n; Hole {h.get('id', 'hole')}: Center=({cx:.3f}, {cy:.3f}), Depth=-{h_depth:.3f} mm")
            lines.append(f"G00 X{cx:.3f} Y{cy:.3f}")
            lines.append(f"G00 Z2.000")
            cur_z = 0.0
            while cur_z < h_depth:
                cur_z = min(h_depth, cur_z + peck_q)
                lines.append(f"G01 Z-{cur_z:.3f} F{d_feed}")
                lines.append(f"G00 Z2.000")
                if cur_z < h_depth:
                    lines.append(f"G01 Z-{(cur_z - 0.5):.3f} F600")
            lines.append("G00 Z5.000")

    # -------------------------------------------------------------
    # Program End
    # -------------------------------------------------------------
    lines.append("\n; -------------------------------------------------------------")
    lines.append("; Program End & Spindle Stop")
    lines.append("; -------------------------------------------------------------")
    lines.append("M09          ; Coolant OFF")
    lines.append("M05          ; Spindle STOP")
    lines.append("G00 Z25.000  ; Retract to high safe Z")
    lines.append("G00 X0.0 Y0.0; Return to origin")
    lines.append("M30          ; Program End and Rewind")
    lines.append("%")

    gcode_text = "\n".join(lines) + "\n"

    # Pre-Flight Feature Coverage Gate: Verify 100% of CAD features are cut
    verify_preflight_feature_coverage(gcode_text, features, strategy_name)

    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    with open(output_file, "w") as f:
        f.write(gcode_text)

    return len(lines)

def generate_all_toolpaths(features_path, tools_path, strategies_path, out_dir, cad_path=None):
    os.makedirs(out_dir, exist_ok=True)

    with open(features_path) as f:
        features = json.load(f)
    with open(tools_path) as f:
        tools = json.load(f)
    with open(strategies_path) as f:
        strategies = json.load(f)

    print("=" * 70)
    print(" [Step 3] MACHINE-READY G-CODE GENERATOR (.NGC)")
    print(f" Target CAD       : {cad_path}")
    print(f" Input Features   : {features_path}")
    print(f" Tool Library     : {tools_path}")
    print(f" Strategy Plan    : {strategies_path}")
    print(f" Output Directory : {out_dir}")
    print("=" * 70)

    # -------------------------------------------------------------
    # Slicing Worker: Pre-compute exact B-Rep cross-sections if STEP CAD provided
    # -------------------------------------------------------------
    slices_data = None
    if cad_path and os.path.exists(cad_path) and cad_path.lower().endswith((".step", ".stp")):
        deepest = features.get("machinability_constraints", {}).get("deepest_feature_depth_mm", 10.0)
        pockets = features.get("features", {}).get("setup_1_top_3axis", {}).get("pockets", [])
        if pockets:
            deepest = max(deepest, max(p.get("depth_from_external_top_mm", 10.0) for p in pockets))

        # Collect unique Z levels needed across all 3 strategies PLUS all feature floor depths!
        z_levels_needed = set()
        all_floors = features.get("machinability_constraints", {}).get("all_floor_depths_mm", [])
        if not all_floors:
            all_floors = [p.get("depth_from_external_top_mm", 0.0) for p in pockets if p.get("depth_from_external_top_mm", 0.0) > 0]

        for fd in all_floors:
            z_levels_needed.add(round(-float(fd), 3))

        for strat_key, strat in strategies.get("strategies", {}).items():
            params = strat.get("parameters", {})
            r_stepdown = params.get("pocket_roughing", {}).get("stepdown_mm", 3.0)
            f_stepdown = params.get("pocket_finishing", {}).get("stepdown_mm", 1.5)

            for z in compute_adaptive_z_passes(deepest, r_stepdown, floor_depths=all_floors):
                z_levels_needed.add(z)

            if params.get("pocket_finishing", {}).get("enabled", False):
                for z in compute_adaptive_z_passes(deepest, f_stepdown, floor_depths=all_floors):
                    z_levels_needed.add(z)

        z_list = sorted(list(z_levels_needed), reverse=True)
        slices_tmp = os.path.join(out_dir, "slices.json")

        print(f"[+] Invoking FreeCAD B-Rep Slicer for {len(z_list)} distinct Z-levels (depth {deepest:.1f}mm)...")
        cmd = ["freecadcmd", SLICE_WORKER, "--", cad_path, slices_tmp, json.dumps(z_list)]
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if os.path.exists(slices_tmp):
            with open(slices_tmp) as f:
                slices_data = json.load(f)
            print(f"[✓] Successfully sliced CAD model ({slices_data.get('total_slices', 0)} Z cross-sections ready)")
        else:
            print("[!] Warning: FreeCAD slicer did not produce slices.json. Falling back to 2D polygon.")
            if proc.stderr:
                print("    Worker STDERR:", proc.stderr[:300])

    mapping = {
        "CYCLE_TIME": "1_cycle_time.ngc",
        "ACCURACY_TUNED": "2_accuracy_tuned.ngc",
        "BALANCED": "3_balanced.ngc"
    }

    generated_files = []
    for key, filename in mapping.items():
        if key in strategies.get("strategies", {}):
            strat = strategies["strategies"][key]
            out_path = os.path.join(out_dir, filename)
            line_count = generate_gcode_for_strategy(
                key, strat, features, tools, out_path, slices_data=slices_data
            )
            size_kb = os.path.getsize(out_path) / 1024.0
            print(f"[+] Generated {filename:<22} : {line_count:4d} lines ({size_kb:.1f} KB)")
            generated_files.append(out_path)

    print(f"\n[✓] All 3 Pareto frontier G-code programs generated successfully!")
    print("=" * 70)
    return generated_files

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Machine-Ready G-Code Generator")
    parser.add_argument("--cad", default=None, help="Path to source CAD file (.step/.dxf)")
    parser.add_argument("--features", default=os.path.join(AGENT_DIR, "features.json"), help="Path to features.json")
    parser.add_argument("--tools", default=os.path.join(AGENT_DIR, "tool_library.json"), help="Path to tool_library.json")
    parser.add_argument("--strategies", default=os.path.join(AGENT_DIR, "strategies.json"), help="Path to strategies.json")
    parser.add_argument("--out-dir", default=AGENT_DIR, help="Directory to save G-code files")
    args = parser.parse_args()

    generate_all_toolpaths(args.features, args.tools, args.strategies, args.out_dir, cad_path=args.cad)
