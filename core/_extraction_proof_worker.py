#!/usr/bin/env python3
"""
Gate A: Feature Extraction Proof Worker (FreeCAD B-Rep Engine)
Mathematically verifies that extracted CAD features reconstruct the nominal
material removal volume (stock - CAD_solid).

Usage:
    freecadcmd core/_extraction_proof_worker.py <cad_step_file> <features_json> <output_json> [tolerance_pct]
"""

import sys
import os
import math
import json
import FreeCAD
import Part

def run_extraction_proof(step_path, features_path, output_path, tolerance_pct=2.5):
    print("=" * 70)
    print(" [*] GATE A: B-REP FEATURE EXTRACTION MATHEMATICAL PROOF")
    print(f" CAD Model     : {step_path}")
    print(f" Features JSON : {features_path}")
    print(f" Output JSON   : {output_path}")
    print(f" Tolerance Pct : {tolerance_pct}%")
    print("=" * 70)

    if not os.path.exists(step_path):
        raise FileNotFoundError(f"CAD STEP file not found: {step_path}")
    if not os.path.exists(features_path):
        raise FileNotFoundError(f"Features JSON not found: {features_path}")

    # 1. Load exact B-Rep CAD model
    shape = Part.Shape()
    shape.read(step_path)
    cad_volume = shape.Volume
    bbox = shape.BoundBox

    with open(features_path, "r") as f:
        features_data = json.load(f)

    # 2. Construct raw stock solid
    stock_req = features_data.get("stock_requirements", {})
    bounds = stock_req.get("bounds", {})
    if "min" in bounds and "max" in bounds:
        x_min, y_min, z_min = bounds["min"]
        x_max, y_max, z_max = bounds["max"]
    else:
        x_min, y_min, z_min = bbox.XMin, bbox.YMin, bbox.ZMin
        x_max, y_max, z_max = bbox.XMax, bbox.YMax, bbox.ZMax

    dx = x_max - x_min
    dy = y_max - y_min
    dz = z_max - z_min
    stock_solid = Part.makeBox(dx, dy, dz, FreeCAD.Vector(x_min, y_min, z_min))
    stock_volume = stock_solid.Volume

    # 3. Compute nominal material to remove
    s_nominal = stock_solid.cut(shape)
    s_nominal_volume = round(s_nominal.Volume, 3)
    top_z = bbox.ZMax

    # 4. Reconstruct feature solids from extracted features
    feat_container = features_data.get("features", {})
    if "setup_1_top_3axis" in feat_container:
        pockets = feat_container["setup_1_top_3axis"].get("pockets", [])
        holes = feat_container["setup_1_top_3axis"].get("vertical_holes", [])
        slanted = feat_container["setup_1_top_3axis"].get("slanted_surfaces", [])
    elif "primary_setup_top_3axis" in feat_container:
        pockets = feat_container["primary_setup_top_3axis"].get("pockets", [])
        holes = feat_container["primary_setup_top_3axis"].get("holes", [])
        slanted = []
    else:
        pockets = feat_container.get("pockets", [])
        holes = feat_container.get("holes", [])
        slanted = []

    feature_solids = []
    per_feature_results = []

    # Reconstruct pockets
    for p in pockets:
        p_id = p.get("id", "unknown_pocket")
        pts = p.get("boundary_polygon_xy", [])
        if len(pts) < 3:
            continue

        depth = p.get("depth_from_external_top_mm", p.get("depth_mm", 0.0))
        if depth <= 0.0:
            floor_z = p.get("floor_z_mm")
            if floor_z is not None:
                depth = top_z - floor_z
            else:
                depth = 5.0

        # Build closed polygon wire at top_z
        poly_pts = [FreeCAD.Vector(pt[0], pt[1], top_z) for pt in pts]
        if poly_pts[0].distanceToPoint(poly_pts[-1]) > 1e-4:
            poly_pts.append(poly_pts[0])

        wire = Part.makePolygon(poly_pts)
        face = Part.Face(wire)
        # Extrude downward by depth
        solid = face.extrude(FreeCAD.Vector(0, 0, -depth))

        # Cut island solids if any
        for island_pts in p.get("island_polygons_xy", []):
            if len(island_pts) >= 3:
                ipts = [FreeCAD.Vector(pt[0], pt[1], top_z) for pt in island_pts]
                if ipts[0].distanceToPoint(ipts[-1]) > 1e-4:
                    ipts.append(ipts[0])
                iwire = Part.makePolygon(ipts)
                iface = Part.Face(iwire)
                isolid = iface.extrude(FreeCAD.Vector(0, 0, -depth))
                solid = solid.cut(isolid)

        feature_solids.append(solid)
        per_feature_results.append({
            "id": p_id,
            "type": p.get("type", "pocket"),
            "reconstructed_volume_mm3": round(solid.Volume, 3)
        })

    # Reconstruct vertical holes
    for h in holes:
        h_id = h.get("id", "unknown_hole")
        r = h.get("radius_mm", h.get("diameter_mm", 6.0) / 2.0)
        cx, cy = h.get("center_xy_mm", [0.0, 0.0])
        h_depth = h.get("depth_from_external_top_mm", h.get("depth_mm", dz))

        cyl = Part.makeCylinder(r, h_depth, FreeCAD.Vector(cx, cy, top_z), FreeCAD.Vector(0, 0, -1))
        feature_solids.append(cyl)
        per_feature_results.append({
            "id": h_id,
            "type": "vertical_hole",
            "reconstructed_volume_mm3": round(cyl.Volume, 3)
        })

    if not feature_solids:
        print("[!] No feature solids could be reconstructed from features.json")
        result = {
            "status": "FAIL",
            "reason": "ZERO_FEATURE_SOLIDS",
            "target_removal_volume_mm3": s_nominal_volume,
            "reconstructed_feature_volume_mm3": 0.0,
            "total_residual_volume_mm3": s_nominal_volume,
            "fidelity_pct": 0.0
        }
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        return False

    # 5. Boolean Union of all feature solids
    total_union = feature_solids[0]
    for s in feature_solids[1:]:
        total_union = total_union.fuse(s)

    reconstructed_volume = round(total_union.Volume, 3)

    # 6. Boolean Residual Analysis:
    # - Unmatched = Material in nominal CAD removal that feature extraction missed
    # - Extra = Material in feature solids that cuts into the nominal CAD part (gouging)
    # Use fuzzy tolerance (0.05mm) to handle co-planar coincident face boundaries
    try:
        unmatched_shape = s_nominal.cut(total_union, 0.05)
        extra_shape = total_union.cut(s_nominal, 0.05)
    except Exception:
        unmatched_shape = s_nominal.cut(total_union)
        extra_shape = total_union.cut(s_nominal)

    unmatched_volume = round(unmatched_shape.Volume, 3)
    extra_volume = round(extra_shape.Volume, 3)
    total_residual = round(unmatched_volume + extra_volume, 3)

    fidelity_pct = round(max(0.0, (1.0 - (unmatched_volume / max(1.0, s_nominal_volume))) * 100.0), 2)
    max_allowed_residual = max(5.0, s_nominal_volume * (tolerance_pct / 100.0))

    passed = (unmatched_volume <= max_allowed_residual) and (extra_volume <= max_allowed_residual) and (total_residual <= max_allowed_residual)

    result = {
        "status": "PASS" if passed else "FAIL",
        "gate": "GATE_A_EXTRACTION_PROOF",
        "cad_file": os.path.basename(step_path),
        "target_removal_volume_mm3": s_nominal_volume,
        "reconstructed_feature_volume_mm3": reconstructed_volume,
        "unmatched_volume_mm3": unmatched_volume,
        "extra_volume_mm3": extra_volume,
        "total_residual_volume_mm3": total_residual,
        "volumetric_fidelity_pct": fidelity_pct,
        "tolerance_pct": tolerance_pct,
        "max_allowed_residual_mm3": round(max_allowed_residual, 3),
        "per_feature_reconstruction": per_feature_results
    }

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f" [+] Stock Volume                 : {stock_volume:.2f} mm³")
    print(f" [+] Target Removal Volume (ΔV)   : {s_nominal_volume:.2f} mm³")
    print(f" [+] Reconstructed Feature Volume : {reconstructed_volume:.2f} mm³")
    print(f" [+] Unmatched Volume (Omitted)   : {unmatched_volume:.2f} mm³")
    print(f" [+] Extra Volume (Gouged/Extra)  : {extra_volume:.2f} mm³")
    print(f" [+] Total Residual Volume        : {total_residual:.2f} mm³")
    print(f" [+] Volumetric Fidelity          : {fidelity_pct:.2f}%")
    print(f" [+] Gate A Verdict               : {'[PASS] APPROVED' if passed else '[FAIL] DISCREPANCY DETECTED'}")
    print("=" * 70)

    return passed


try:
    script_idx = -1
    for i, arg in enumerate(sys.argv):
        if "_extraction_proof_worker.py" in arg:
            script_idx = i
            break
    worker_args = sys.argv[script_idx + 1:] if script_idx >= 0 else []
    if worker_args and worker_args[0] == "--":
        worker_args = worker_args[1:]

    if len(worker_args) < 3:
        print("Usage: freecadcmd _extraction_proof_worker.py <cad_step> <features_json> <output_json> [tolerance_pct]")
        sys.exit(1)

    step_arg = worker_args[0]
    features_arg = worker_args[1]
    out_arg = worker_args[2]
    tol_arg = float(worker_args[3]) if len(worker_args) > 3 else 2.5

    success = run_extraction_proof(step_arg, features_arg, out_arg, tolerance_pct=tol_arg)
    sys.exit(0 if success else 1)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
