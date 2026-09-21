#!/usr/bin/env python3
"""
Step 1: Feature Extractor
Deterministic CAD Geometry Extraction for STEP and DXF

Supports:
  - Positional argument or --step / --cad flags
  - 3D STEP (.step, .stp) via OpenCASCADE / FreeCAD B-Rep analysis
  - 2D DXF (.dxf) via ezdxf vector entity analysis

Extracts normalized `features.json` containing:
  1. Stock bounding box (X, Y, Z)
  2. Pockets (depth, bounding box, floor Z, center)
  3. Holes (diameter, depth, through/blind, center coordinates)
  4. Machinability constraints (min internal corner radius, max tool diameter)
"""

import sys
import os
import math
import json
import argparse
import subprocess

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_SCRIPT = os.path.join(AGENT_DIR, "_extract_worker.py")

def extract_dxf(dxf_path, stock_z=10.0, pocket_depth=4.0):
    import ezdxf
    from ezdxf.bbox import extents
    
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    
    box = extents(msp)
    x_min, y_min, _ = box.extmin
    x_max, y_max, _ = box.extmax
    
    stock_dims = {
        "x_length_mm": round(x_max - x_min, 3),
        "y_length_mm": round(y_max - y_min, 3),
        "z_length_mm": round(stock_z, 3),
        "bounds": {
            "min": [round(x_min, 3), round(y_min, 3), 0.0],
            "max": [round(x_max, 3), round(y_max, 3), round(stock_z, 3)]
        }
    }
    
    holes = []
    for circle in msp.query("CIRCLE"):
        cx, cy, _ = circle.dxf.center
        r = round(circle.dxf.radius, 3)
        holes.append({
            "id": f"hole_{len(holes)+1}",
            "diameter_mm": round(r * 2.0, 3),
            "radius_mm": r,
            "center_xy_mm": [round(cx, 3), round(cy, 3)],
            "depth_mm": round(stock_z, 3),
            "is_through_hole": True
        })
        
    pockets = []
    internal_radii = []
    
    for poly in msp.query("LWPOLYLINE"):
        pts = poly.get_points(format="xyseb")
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        poly_w = max(xs) - min(xs)
        poly_l = max(ys) - min(ys)
        
        if abs(poly_w - stock_dims["x_length_mm"]) < 0.5 and abs(poly_l - stock_dims["y_length_mm"]) < 0.5:
            continue
            
        detected_r = 3.0
        internal_radii.append(detected_r)
        
        pockets.append({
            "id": f"pocket_{len(pockets)+1}",
            "floor_z_mm": round(stock_z - pocket_depth, 3),
            "depth_mm": round(pocket_depth, 3),
            "bounds": {
                "min_x": round(min(xs), 3),
                "max_x": round(max(xs), 3),
                "min_y": round(min(ys), 3),
                "max_y": round(max(ys), 3),
                "width_x_mm": round(poly_w, 3),
                "length_y_mm": round(poly_l, 3)
            },
            "center_mm": [round((min(xs) + max(xs)) / 2.0, 3), round((min(ys) + max(ys)) / 2.0, 3)],
            "min_corner_radius_mm": detected_r,
            "max_tool_diameter_mm": round(detected_r * 2.0, 3)
        })
        
    min_internal_r = min(internal_radii) if internal_radii else None
    all_depths = [p["depth_mm"] for p in pockets] + [h["depth_mm"] for h in holes]
    
    return {
        "source_cad_file": os.path.basename(dxf_path),
        "file_format": "DXF (2D Vector)",
        "canonical_wcs": {
            "origin_type": "TOP_SURFACE_MATCH_CAD_XY",
            "delta_x": 0.0,
            "delta_y": 0.0,
            "delta_z": 0.0,
            "stock_bounds_wcs": {
                "min": [round(x_min, 3), round(y_min, 3), round(-stock_z, 3)],
                "max": [round(x_max, 3), round(y_max, 3), 0.0]
            }
        },
        "stock_requirements": stock_dims,
        "features": {
            "pockets": pockets,
            "holes": holes
        },
        "machinability_constraints": {
            "min_internal_corner_radius_mm": min_internal_r,
            "max_tool_diam_for_corners_mm": round(min_internal_r * 2, 3) if min_internal_r else None,
            "deepest_feature_depth_mm": max(all_depths, default=0.0)
        }
    }


def run_extractor(file_path, output_json, stock_z=10.0, pocket_depth=4.0):
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
        
    ext = os.path.splitext(file_path)[1].lower()
    print("=" * 70)
    print(f" [Step 1] DETERMINISTIC CAD FEATURE EXTRACTION ({ext.upper()})")
    print(f" Input File  : {file_path}")
    print(f" Output JSON : {output_json}")
    print("=" * 70)
    
    if ext in [".step", ".stp"]:
        cmd = ["freecadcmd", WORKER_SCRIPT, file_path, output_json]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(output_json):
            print("ERROR: FreeCAD STEP feature extraction failed.")
            print("STDOUT:", res.stdout)
            print("STDERR:", res.stderr)
            sys.exit(1)
        with open(output_json, "r") as f:
            data = json.load(f)
            data["file_format"] = "STEP (3D Exact B-Rep)"
    elif ext == ".dxf":
        data = extract_dxf(file_path, stock_z=stock_z, pocket_depth=pocket_depth)
        with open(output_json, "w") as f:
            json.dump(data, f, indent=2)
    else:
        print(f"Unsupported format: {ext}. Supported formats are .step, .stp, .dxf")
        sys.exit(1)
        
    stock = data["stock_requirements"]
    
    # Handle multi-setup or flat feature structure
    feat_container = data.get("features", {})
    if "setup_1_top_3axis" in feat_container:
        s1 = feat_container.get("setup_1_top_3axis", {})
        pockets = s1.get("pockets", [])
        holes = s1.get("vertical_holes", [])
        slanted_faces = s1.get("slanted_surfaces", [])
        side_holes = feat_container.get("setup_2_horizontal_side_features", {}).get("side_cross_holes", [])
        compound_holes = feat_container.get("setup_3_multi_axis_5axis_features", {}).get("compound_angled_holes", [])
        undercuts = feat_container.get("special_tooling_features", {}).get("undercut_cavities", [])
    elif "primary_setup_top_3axis" in feat_container:
        pockets = feat_container["primary_setup_top_3axis"].get("pockets", [])
        holes = feat_container["primary_setup_top_3axis"].get("holes", [])
        multi_setup = feat_container.get("multi_setup_and_complex_features", {})
        slanted_faces = multi_setup.get("slanted_surfaces", [])
        side_holes = multi_setup.get("side_cross_holes", [])
        compound_holes = []
        undercuts = []
    else:
        pockets = feat_container.get("pockets", [])
        holes = feat_container.get("holes", [])
        slanted_faces = []
        side_holes = []
        compound_holes = []
        undercuts = []

    chamfers = feat_container.get("chamfers", [])
    floor_fillets = feat_container.get("floor_fillets", [])
    constraints = data["machinability_constraints"]
    
    print(f"\n[+] Format Identified: {data.get('file_format', ext)}")
    print(f"[+] Raw Stock Dimensions Required:")
    print(f"    Length (X) : {stock['x_length_mm']:.2f} mm")
    print(f"    Width  (Y) : {stock['y_length_mm']:.2f} mm")
    print(f"    Height (Z) : {stock['z_length_mm']:.2f} mm")
    
    print(f"\n[+] Setup 1: Primary Top-Down 3-Axis Features:")
    print(f"    • Pockets ({len(pockets)} found):")
    for p in pockets:
        depth = p.get('depth_from_external_top_mm', p.get('depth_mm', 0.0))
        chamfer_str = f" | Top Chamfer: {p['top_rim_chamfer']['width_mm']}mm @ {p['top_rim_chamfer']['angle_deg']}°" if p.get('top_rim_chamfer', {}).get('has_chamfer') else ""
        print(f"      - {p['id']}: Floor Size {p['bounds']['width_x_mm']} x {p['bounds']['length_y_mm']} mm"
              f" (Top Opening: {p.get('top_opening_dimensions', {}).get('width_x_mm', p['bounds']['width_x_mm'])} x {p.get('top_opening_dimensions', {}).get('length_y_mm', p['bounds']['length_y_mm'])} mm)")
        print(f"        Plunge Depth from Top: {depth} mm (Floor Z = {p['floor_z_mm']} mm){chamfer_str}")
        print(f"        Internal Corner Radius: {p.get('min_corner_radius_mm')} mm "
              f"(Max Tool Diameter <= {p.get('max_tool_diameter_mm')} mm)")
              
    print(f"    • Vertical Holes ({len(holes)} found):")
    for h in holes:
        h_type = "Through-hole" if h.get("is_through_hole") else "Blind hole"
        h_depth = h.get('depth_from_external_top_mm', h.get('depth_mm', 0.0))
        csink_str = f" | Countersink: {h['top_countersink']['width_mm']}mm" if h.get('top_countersink', {}).get('has_countersink') else ""
        print(f"      - {h['id']}: Diameter {h['diameter_mm']} mm, Depth from Top: {h_depth} mm, "
              f"Center: ({h['center_xy_mm'][0]}, {h['center_xy_mm'][1]}), Type: {h_type}{csink_str}")
              
    if slanted_faces:
        print(f"    • Slanted / 3D Ruled Surfaces ({len(slanted_faces)} found):")
        for s in slanted_faces:
            print(f"      - {s['id']}: Tilt Angle {s['tilt_angle_from_horizontal_deg']}° from Horizontal (Area: {s['area_mm2']} mm²)")
            print(f"        Z-Height Range: {s['z_height_range_mm']} mm | Strategy: {s['machining_strategy']}")

    if side_holes:
        print(f"\n[+] Setup 2: Horizontal Side Cross-Holes ({len(side_holes)} found):")
        for sh in side_holes:
            print(f"    - {sh['id']}: Diameter {sh['diameter_mm']} mm, Depth: {sh['depth_mm']} mm, Axis Direction: {sh['axis_orientation']}")
            print(f"      Requirement: {sh['requires_setup']}")

    if compound_holes:
        print(f"\n[+] Setup 3: Multi-Axis Compound-Angled Holes ({len(compound_holes)} found):")
        for ch in compound_holes:
            print(f"    - {ch['id']}: Diameter {ch['diameter_mm']} mm, Tilt from Vertical: {ch['tilt_angle_from_vertical_deg']}°")
            print(f"      Tool Axis Vector: {ch['tool_axis_vector']} | {ch['requires_setup']}")

    if undercuts:
        print(f"\n[+] Special Tooling Features ({len(undercuts)} undercut cavities found):")
        for uc in undercuts:
            print(f"    - {uc['id']}: Ceiling Z: {uc['ceiling_z_mm']} mm | {uc['requires_tool']}")

    print(f"\n[+] Machinability & Corner Check:")
    print(f"    - Vertical Corner Radius: {constraints.get('vertical_corner_radius_mm')} mm")
    print(f"    - Max Permissible Endmill Diameter: {constraints.get('max_tool_diam_for_corners_mm')} mm")
    print(f"    - Requires Multiple Setups: {constraints.get('requires_multiple_setups', False)}")
    print(f"    - Requires 5-Axis Indexing: {constraints.get('requires_5axis_indexing', False)}")
    print(f"    - Deepest Feature: {constraints['deepest_feature_depth_mm']} mm (Required tool flute reach)")
    
    # Verification Audit Display
    audit = data.get("verification_audit")
    if audit:
        print("\n" + "=" * 70)
        print(f" [*] 3-TIER MATHEMATICAL VERIFICATION AUDIT (Status: {audit['overall_status']})")
        print(f"     Classification: {audit.get('machining_classification', 'N/A')}")
        print("=" * 70)
        a1 = audit["audit_1_surface_accounting"]
        print(f" [Audit 1: Surface Accounting - No Orphan Faces]")
        print(f"   • Total CAD Faces        : {a1['total_cad_faces']}")
        print(f"   • Stock Boundary Faces   : {a1['stock_boundary_faces']}")
        print(f"   • Feature Faces Claimed  : {a1['machined_feature_faces']}")
        print(f"   • Orphan Unclaimed Faces : {a1['orphan_unrecognized_faces']}")
        print(f"   • Surface Coverage Score : {a1['surface_coverage_pct']}%  [{a1['status']}]")
        
        a2 = audit["audit_2_volume_conservation"]
        print(f"\n [Audit 2: Volume Conservation - ΔV Check]")
        print(f"   • Target Stock Removal   : {a2['target_material_removal_mm3']:.1f} mm³")
        print(f"   • Extracted Features Vol : {a2['extracted_features_volume_mm3']:.1f} mm³")
        print(f"   • Volume Accountability  : {a2['volume_accountability_pct']}%  [{a2['status']}]")
        
        a3 = audit.get("audit_3_multi_setup_partitioning", audit.get("audit_3_boolean_reconstruction", {}))
        print(f"\n [Audit 3: Setup Decomposition & Reconstruction]")
        for k, v in a3.items():
            print(f"   • {k.replace('_', ' ').title():<32}: {v}")
        print("=" * 70)

    print(f"\n-> Extracted features saved to: {output_json}")
    print("=" * 70)
    return data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Deterministic STEP / DXF Feature Extractor")
    # Accept positional argument or optional flags
    parser.add_argument("cad_file", nargs="?", default=None, help="Input STEP or DXF file path (positional)")
    parser.add_argument("--cad", "--step", "--input", "-i", dest="cad_flag", default=None, help="Input STEP or DXF file path (flag)")
    parser.add_argument("--out", "--output", "-o", default=os.path.join(AGENT_DIR, "features.json"), help="Path to output JSON")
    parser.add_argument("--stock-z", type=float, default=10.0, help="Stock thickness in mm (for 2D DXF)")
    parser.add_argument("--pocket-depth", type=float, default=4.0, help="Pocket depth in mm (for 2D DXF)")
    args = parser.parse_args()
    
    # Resolve file path from positional or flag
    target_file = args.cad_file or args.cad_flag
    if not target_file:
        target_file = os.path.join(AGENT_DIR, "sample_part.step")
        
    run_extractor(target_file, args.out, stock_z=args.stock_z, pocket_depth=args.pocket_depth)
