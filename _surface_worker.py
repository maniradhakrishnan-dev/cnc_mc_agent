#!/usr/bin/env python3
"""
FreeCAD Worker for Step 5: Surface Deviation & Mesh Analysis
Measures exact CAD volume, STL mesh volume, bounding boxes, and floor depths.
Uses robust facet cavity integration with triangle deduplication and through-hole
geometry verification.
"""

import sys
import os
import json
import FreeCAD
import Part
import numpy as np

def read_stl_numpy(filename):
    """Reads binary STL into structured numpy array."""
    if not os.path.exists(filename):
        return None
    with open(filename, 'rb') as f:
        header = f.read(80)
        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            return None
        count = int.from_bytes(count_bytes, 'little')
        data = np.frombuffer(f.read(count * 50), dtype=np.dtype([
            ('normal', np.float32, (3,)),
            ('v0', np.float32, (3,)),
            ('v1', np.float32, (3,)),
            ('v2', np.float32, (3,)),
            ('attr', np.uint16)
        ]))
    return data

def analyze_shapes(step_file, stl_files_map, features_path=None, cad_stl_out=None):
    results = {}
    
    # 1. Target CAD model (STEP 3D B-Rep)
    results["target_cad"] = None
    cad_shape_wcs = None
    if os.path.exists(step_file) and step_file.lower().endswith((".step", ".stp")):
        try:
            cad = Part.read(step_file)
            bb = cad.BoundBox
            results["target_cad"] = {
                "volume_mm3": round(cad.Volume, 3),
                "bound_box": {
                    "min_xyz": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)],
                    "max_xyz": [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)],
                    "dimensions_xyz": [
                        round(bb.XLength, 3),
                        round(bb.YLength, 3),
                        round(bb.ZLength, 3)
                    ]
                }
            }

            # Translate CAD solid to CAMotics WCS coordinates (Z_top = 0.0)
            cad_wcs = cad.copy()
            cad_wcs.translate(FreeCAD.Vector(0, 0, -bb.ZMax))
            cad_shape_wcs = cad_wcs

            if cad_stl_out:
                try:
                    cad_wcs.exportStl(cad_stl_out)
                except Exception as ex:
                    print("Nominal STL export warning:", ex)

        except Exception as e:
            print("CAD read warning:", e)

    elif os.path.exists(step_file) and step_file.lower().endswith(".dxf"):
        try:
            feat = {}
            if features_path and os.path.exists(features_path):
                with open(features_path) as f:
                    feat = json.load(f)
            s_req = feat.get("stock_requirements", {})
            sx = s_req.get("x_length_mm", 100.0)
            sy = s_req.get("y_length_mm", 80.0)
            sz = s_req.get("z_length_mm", 10.0)
            stock_solid = Part.makeBox(sx, sy, sz)
            stock_solid.translate(FreeCAD.Vector(0, 0, -sz))
            
            pockets = feat.get("features", {}).get("setup_1_top_3axis", {}).get("pockets", [])
            for p in pockets:
                p_depth = p.get("depth_mm", 4.0)
                bounds = p.get("bounds", {})
                px_min = bounds.get("min_x", 25.0)
                py_min = bounds.get("min_y", 20.0)
                px_len = bounds.get("x_len", p.get("length_mm", 50.0))
                py_len = bounds.get("y_len", p.get("width_mm", 40.0))
                p_box = Part.makeBox(px_len, py_len, p_depth, FreeCAD.Vector(px_min, py_min, -p_depth))
                stock_solid = stock_solid.cut(p_box)

            holes = feat.get("features", {}).get("setup_1_top_3axis", {}).get("vertical_holes", [])
            for h in holes:
                hx, hy = h.get("x", 0.0), h.get("y", 0.0)
                hr = h.get("radius_mm", h.get("diameter_mm", 6.0) / 2.0)
                h_depth = h.get("depth_mm", sz)
                cyl = Part.makeCylinder(hr, h_depth, FreeCAD.Vector(hx, hy, -h_depth), FreeCAD.Vector(0, 0, 1))
                stock_solid = stock_solid.cut(cyl)

            bb = stock_solid.BoundBox
            results["target_cad"] = {
                "volume_mm3": round(stock_solid.Volume, 3),
                "bound_box": {
                    "min_xyz": [round(bb.XMin, 3), round(bb.YMin, 3), round(bb.ZMin, 3)],
                    "max_xyz": [round(bb.XMax, 3), round(bb.YMax, 3), round(bb.ZMax, 3)],
                    "dimensions_xyz": [round(bb.XLength, 3), round(bb.YLength, 3), round(bb.ZLength, 3)]
                }
            }
            cad_shape_wcs = stock_solid
            if cad_stl_out:
                stock_solid.exportStl(cad_stl_out)
        except Exception as e:
            print("DXF 3D solid construction warning:", e)

    # 2. Extract stock envelope and through-holes from features.json
    stock_top_z = 0.0
    stock_vol = 288000.0
    feat_data = {}
    if features_path and os.path.exists(features_path):
        try:
            with open(features_path) as f:
                feat_data = json.load(f)
            s_req = feat_data.get("stock_requirements", {})
            sx = s_req.get("x_length_mm", 120.0)
            sy = s_req.get("y_length_mm", 80.0)
            sz = s_req.get("z_length_mm", 30.0)
            stock_vol = sx * sy * sz
            sb = s_req.get("bounds", {})
            if "max" in sb:
                stock_top_z = 0.0
        except Exception as e:
            print("Features read warning:", e)

    vertical_holes = feat_data.get("features", {}).get("setup_1_top_3axis", {}).get("vertical_holes", [])

    # 3. Each cut STL mesh analysis
    results["simulated_meshes"] = {}
    for strat_key, stl_path in stl_files_map.items():
        if not os.path.exists(stl_path):
            continue
            
        stl_data = read_stl_numpy(stl_path)
        if stl_data is None or len(stl_data) == 0:
            continue

        v0 = stl_data['v0']
        v1 = stl_data['v1']
        v2 = stl_data['v2']
        normals = stl_data['normal']

        # Bounding box
        all_v = np.vstack([v0, v1, v2])
        min_xyz = [float(all_v[:, 0].min()), float(all_v[:, 1].min()), float(all_v[:, 2].min())]
        max_xyz = [float(all_v[:, 0].max()), float(all_v[:, 1].max()), float(all_v[:, 2].max())]
        dim_xyz = [max_xyz[0] - min_xyz[0], max_xyz[1] - min_xyz[1], max_xyz[2] - min_xyz[2]]

        # Stock top Z reference (always 0.0 in CAMotics WCS)
        mesh_top_z = max_xyz[2]
        ref_top_z = stock_top_z

        # 3A. Through-hole cutting verification and volume accounting
        through_holes_vol = 0.0
        hole_exclusion_zones = []
        for h in vertical_holes:
            if not h.get("is_through_hole", False):
                continue
            cx, cy = h["center_xy_mm"]
            r = h["radius_mm"]
            depth = h.get("depth_from_external_top_mm", h.get("depth_mm", 10.0))
            hole_exclusion_zones.append((cx, cy, r + 1.0))

            dist = np.hypot(all_v[:, 0] - cx, all_v[:, 1] - cy)
            top_inside = all_v[(dist < (r * 0.5)) & (all_v[:, 2] > (ref_top_z - 1.0))]
            wall_filter = (dist >= (r * 0.6)) & (dist <= (r * 1.4)) & (all_v[:, 2] < -2.0) & (all_v[:, 2] > (-depth + 2.0))
            wall_pts = all_v[wall_filter]

            if len(top_inside) == 0 and len(wall_pts) > 0:
                actual_r = float(np.median(dist[wall_filter]))
                h_vol = np.pi * (actual_r ** 2) * depth
                through_holes_vol += h_vol

        # 3B. Deduplicate triangles for blind cavity & pocket integration
        tri = np.stack([v0, v1, v2], axis=1)
        tri_r = np.round(tri, 2)
        for i in range(len(tri_r)):
            tri_r[i] = tri_r[i][np.lexsort((tri_r[i, :, 2], tri_r[i, :, 1], tri_r[i, :, 0]))]
        _, u_indices = np.unique(tri_r.reshape(len(tri_r), -1), axis=0, return_index=True)

        u_v0 = v0[u_indices]
        u_v1 = v1[u_indices]
        u_v2 = v2[u_indices]
        u_norm = normals[u_indices]

        area_xy = 0.5 * ((u_v1[:, 0] - u_v0[:, 0]) * (u_v2[:, 1] - u_v0[:, 1]) - (u_v2[:, 0] - u_v0[:, 0]) * (u_v1[:, 1] - u_v0[:, 1]))
        z_mean = (u_v0[:, 2] + u_v1[:, 2] + u_v2[:, 2]) / 3.0
        u_cx = (u_v0[:, 0] + u_v1[:, 0] + u_v2[:, 0]) / 3.0
        u_cy = (u_v0[:, 1] + u_v1[:, 1] + u_v2[:, 1]) / 3.0

        # Exclude through-hole footprints from cavity floor summation
        in_any_hole = np.zeros(len(u_v0), dtype=bool)
        for hx, hy, hr in hole_exclusion_zones:
            in_any_hole |= (np.hypot(u_cx - hx, u_cy - hy) < hr)

        cavity_mask = (u_norm[:, 2] > 0.05) & (z_mean < (ref_top_z - 0.1)) & (area_xy > 1e-6) & (~in_any_hole)
        cavity_vol = float(np.sum(area_xy[cavity_mask] * (ref_top_z - z_mean[cavity_mask])))

        actual_removed_vol = cavity_vol + through_holes_vol
        simulated_part_vol = max(0.0, stock_vol - actual_removed_vol)

        # 3C. Metrological Euclidean Surface Deviation (Point-to-CAD Distance)
        mean_dev_um = 0.0
        max_dev_um = 0.0
        rms_dev_um = 0.0
        p95_dev_um = 0.0

        if cad_shape_wcs is not None and len(all_v) > 0:
            # Mask points to machined cavity (exclude unmachined raw stock bottom and top)
            machined_mask = (all_v[:, 2] > (min_xyz[2] + 0.5)) & (all_v[:, 2] < (ref_top_z - 0.2))
            machined_pts = all_v[machined_mask]
            if len(machined_pts) > 0:
                # Deduplicate points to 0.1mm grid
                u_m_pts = np.unique(np.round(machined_pts, 1), axis=0)
                # Sample up to 250 points for fast, robust evaluation
                if len(u_m_pts) > 250:
                    np.random.seed(42)
                    sample_indices = np.random.choice(len(u_m_pts), size=250, replace=False)
                    sample_pts = u_m_pts[sample_indices]
                else:
                    sample_pts = u_m_pts

                point_dists = []
                for pt in sample_pts:
                    d, _, _ = cad_shape_wcs.distToShape(Part.Vertex(float(pt[0]), float(pt[1]), float(pt[2])))
                    point_dists.append(d)

                if point_dists:
                    d_arr = np.array(point_dists)
                    mean_dev_um = round(float(np.mean(d_arr) * 1000.0), 2)
                    max_dev_um = round(float(np.max(d_arr) * 1000.0), 2)
                    rms_dev_um = round(float(np.sqrt(np.mean(d_arr**2)) * 1000.0), 2)
                    p95_dev_um = round(float(np.percentile(d_arr, 95) * 1000.0), 2)

        results["simulated_meshes"][strat_key] = {
            "volume_mm3": round(simulated_part_vol, 3),
            "actual_removed_vol_mm3": round(actual_removed_vol, 3),
            "cavity_vol_mm3": round(cavity_vol, 3),
            "through_holes_vol_mm3": round(through_holes_vol, 3),
            "stock_volume_mm3": round(stock_vol, 3),
            "point_count": int(len(all_v)),
            "facet_count": int(len(stl_data)),
            "mean_deviation_um": mean_dev_um,
            "max_deviation_um": max_dev_um,
            "rms_deviation_um": rms_dev_um,
            "p95_deviation_um": p95_dev_um,
            "bound_box": {
                "min_xyz": [round(x, 3) for x in min_xyz],
                "max_xyz": [round(x, 3) for x in max_xyz],
                "dimensions_xyz": [round(x, 3) for x in dim_xyz]
            }
        }

    return results

try:
    if len(sys.argv) >= 4:
        input_step = sys.argv[-3]
        out_json = sys.argv[-2]
        features_json = sys.argv[-1]
        stl_dir = os.path.dirname(os.path.abspath(out_json))
    elif len(sys.argv) >= 3:
        input_step = sys.argv[-2]
        out_json = sys.argv[-1]
        stl_dir = os.path.dirname(os.path.abspath(out_json))
        features_json = os.path.join(stl_dir, "features.json")
    else:
        input_step = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_part.step")
        out_json = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mesh_analysis.json")
        stl_dir = os.path.dirname(os.path.abspath(out_json))
        features_json = os.path.join(stl_dir, "features.json")

    stls = {
        "CYCLE_TIME": os.path.join(stl_dir, "1_cycle_time_cut.stl"),
        "ACCURACY_TUNED": os.path.join(stl_dir, "2_accuracy_tuned_cut.stl"),
        "BALANCED": os.path.join(stl_dir, "3_balanced_cut.stl")
    }

    nominal_stl = os.path.join(stl_dir, "nominal_cad.stl")
    res = analyze_shapes(input_step, stls, features_json, cad_stl_out=nominal_stl)
    os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
    with open(out_json, "w") as f:
        json.dump(res, f, indent=2)

    print("__SURFACE_WORKER_SUCCESS__")
    sys.exit(0)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
