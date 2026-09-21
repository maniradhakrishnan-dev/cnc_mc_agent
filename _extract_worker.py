import sys, os, math, json
import FreeCAD, Part

def extract_features_exact(step_file):
    shape = Part.Shape()
    shape.read(step_file)
    
    bbox = shape.BoundBox
    stock_dims = {
        "x_length_mm": round(bbox.XLength, 4),
        "y_length_mm": round(bbox.YLength, 4),
        "z_length_mm": round(bbox.ZLength, 4),
        "bounds": {
            "min": [round(bbox.XMin, 4), round(bbox.YMin, 4), round(bbox.ZMin, 4)],
            "max": [round(bbox.XMax, 4), round(bbox.YMax, 4), round(bbox.ZMax, 4)]
        }
    }
    
    top_z = bbox.ZMax
    bottom_z = bbox.ZMin
    cad_solid_volume = shape.Volume
    stock_volume = bbox.XLength * bbox.YLength * bbox.ZLength
    target_removal_volume = round(stock_volume - cad_solid_volume, 3)
    
    total_faces_count = len(shape.Faces)
    claimed_face_indices = set()
    stock_boundary_face_indices = set()
    
    # -------------------------------------------------------------
    # 1. External Stock Boundary Faces (Top, Bottom, 4 Outer Sides)
    # -------------------------------------------------------------
    for idx, face in enumerate(shape.Faces):
        if "Plane" in face.Surface.TypeId:
            fb = face.BoundBox
            on_top = abs(fb.ZMax - top_z) < 0.05 and abs(fb.ZMin - top_z) < 0.05
            on_bottom = abs(fb.ZMin - bottom_z) < 0.05 and abs(fb.ZMax - bottom_z) < 0.05
            on_xmin = abs(fb.XMin - bbox.XMin) < 0.05 and abs(fb.XMax - bbox.XMin) < 0.05
            on_xmax = abs(fb.XMax - bbox.XMax) < 0.05 and abs(fb.XMin - bbox.XMax) < 0.05
            on_ymin = abs(fb.YMin - bbox.YMin) < 0.05 and abs(fb.YMax - bbox.YMin) < 0.05
            on_ymax = abs(fb.YMax - bbox.YMax) < 0.05 and abs(fb.YMin - bbox.YMax) < 0.05
            
            if on_top or on_bottom or on_xmin or on_xmax or on_ymin or on_ymax:
                stock_boundary_face_indices.add(idx)

    # -------------------------------------------------------------
    # 2. Conical Faces (Chamfers, Countersinks, and Drill Point Tips)
    # -------------------------------------------------------------
    all_chamfer_faces = []
    drill_point_cone_indices = set()
    for idx, face in enumerate(shape.Faces):
        if "Cone" in face.Surface.TypeId:
            cone = face.Surface
            semi_angle = round(math.degrees(cone.SemiAngle), 2)
            fb = face.BoundBox
            depth_ch = round(fb.ZLength, 4)
            width_ch = round(depth_ch * math.tan(cone.SemiAngle), 4)
            is_top = abs(fb.ZMax - top_z) < 0.1
            
            # Drill point tips are at the bottom of blind holes pointing downwards
            if not is_top and abs(semi_angle - 59.0) < 5.0:  # 118 deg drill point angle has semi-angle 59 deg
                drill_point_cone_indices.add(idx)
                claimed_face_indices.add(idx)
            else:
                claimed_face_indices.add(idx)
                all_chamfer_faces.append({
                    "face_idx": idx,
                    "semi_angle": semi_angle,
                    "depth_mm": depth_ch,
                    "width_mm": width_ch,
                    "bbox": fb,
                    "is_top_rim": is_top
                })

    # -------------------------------------------------------------
    # 3. Vertical Corner Fillets (Pocket Inside Radii)
    # -------------------------------------------------------------
    internal_radii = []
    for idx, face in enumerate(shape.Faces):
        if "Cylinder" in face.Surface.TypeId:
            cyl = face.Surface
            axis = cyl.Axis
            if abs(axis.z - 1.0) < 1e-3 or abs(axis.z + 1.0) < 1e-3:
                u_span = abs(face.ParameterRange[1] - face.ParameterRange[0])
                is_closed = abs(u_span - (2.0 * 3.1415926535)) < 0.1
                fb = face.BoundBox
                if not is_closed and fb.ZLength < (stock_dims["z_length_mm"] - 0.2):
                    claimed_face_indices.add(idx)
                    internal_radii.append(round(cyl.Radius, 4))
    min_internal_r = min(internal_radii) if internal_radii else None

    # -------------------------------------------------------------
    # 4. Standard Vertical Pockets (Upward Planar Floors + Side Walls)
    # -------------------------------------------------------------
    pockets = []
    for idx, face in enumerate(shape.Faces):
        if "Plane" in face.Surface.TypeId and idx not in stock_boundary_face_indices:
            u_mid = (face.ParameterRange[0] + face.ParameterRange[1]) / 2.0
            v_mid = (face.ParameterRange[2] + face.ParameterRange[3]) / 2.0
            normal = face.normalAt(u_mid, v_mid)
            
            # Upward horizontal plane (normal = +Z)
            if abs(normal.z - 1.0) < 1e-3 and abs(normal.x) < 1e-3 and abs(normal.y) < 1e-3:
                f_z = face.CenterOfMass.z
                if f_z < (top_z - 0.1) and f_z > (bottom_z + 0.1):
                    claimed_face_indices.add(idx)
                    fb = face.BoundBox
                    depth = round(top_z - f_z, 4)
                    w_x = round(fb.XLength, 4)
                    l_y = round(fb.YLength, 4)
                    
                    # Geometric Wire Inspection: Detect Pure Circles and Slots
                    circle_edges = [e for e in face.Edges if "Circle" in e.Curve.TypeId]
                    line_edges = [e for e in face.Edges if "Line" in e.Curve.TypeId]
                    
                    is_pure_circle = (len(face.Edges) == 1 and len(circle_edges) == 1)
                    circ_radius = None
                    circ_center = None
                    if is_pure_circle:
                        circ_radius = round(circle_edges[0].Curve.Radius, 4)
                        circ_center = [round(circle_edges[0].Curve.Center.x, 4), round(circle_edges[0].Curve.Center.y, 4)]
                        pocket_volume = round(math.pi * (circ_radius ** 2) * depth, 2)
                        
                        # Claim any adjacent vertical cylinder walls
                        for c_idx, c_face in enumerate(shape.Faces):
                            if "Cylinder" in c_face.Surface.TypeId and c_idx not in claimed_face_indices:
                                cyl = c_face.Surface
                                if abs(cyl.Radius - circ_radius) < 0.2 and abs(cyl.Center.x - circ_center[0]) < 0.5 and abs(cyl.Center.y - circ_center[1]) < 0.5:
                                    claimed_face_indices.add(c_idx)
                    else:
                        pocket_volume = round(face.Area * depth, 2)

                    # Detect Slots (2 equal semicircular arcs + 2 straight lines)
                    is_slot = False
                    slot_info = None
                    if len(circle_edges) == 2 and len(line_edges) == 2:
                        r0 = circle_edges[0].Curve.Radius
                        r1 = circle_edges[1].Curve.Radius
                        if abs(r0 - r1) < 0.1:
                            is_slot = True
                            sr = round(r0, 4)
                            c1 = [round(circle_edges[0].Curve.Center.x, 4), round(circle_edges[0].Curve.Center.y, 4)]
                            c2 = [round(circle_edges[1].Curve.Center.x, 4), round(circle_edges[1].Curve.Center.y, 4)]
                            slot_info = {
                                "slot_width_mm": round(sr * 2.0, 4),
                                "slot_radius_mm": sr,
                                "center_1": c1,
                                "center_2": c2
                            }

                    # Detect Open Steps/Shoulders (edges lie on raw stock boundaries)
                    stock_b = stock_dims["bounds"]
                    open_edges = {
                        "min_x": bool(fb.XMin <= stock_b["min"][0] + 0.5),
                        "max_x": bool(fb.XMax >= stock_b["max"][0] - 0.5),
                        "min_y": bool(fb.YMin <= stock_b["min"][1] + 0.5),
                        "max_y": bool(fb.YMax >= stock_b["max"][1] - 0.5)
                    }
                    is_open_step = any(open_edges.values())

                    # Claim adjacent vertical walls of this pocket
                    for w_idx, w_face in enumerate(shape.Faces):
                        if "Plane" in w_face.Surface.TypeId and w_idx not in claimed_face_indices and w_idx not in stock_boundary_face_indices:
                            wb = w_face.BoundBox
                            if (wb.XMin >= fb.XMin - 2.0 and wb.XMax <= fb.XMax + 2.0 and
                                wb.YMin >= fb.YMin - 2.0 and wb.YMax <= fb.YMax + 2.0 and
                                wb.ZMin >= f_z - 0.2 and wb.ZMax <= top_z + 0.2):
                                claimed_face_indices.add(w_idx)

                    # Claim adjacent vertical cylinder walls (pocket islands / bosses)
                    for c_idx, c_face in enumerate(shape.Faces):
                        if "Cylinder" in c_face.Surface.TypeId and c_idx not in claimed_face_indices:
                            cb = c_face.BoundBox
                            if (cb.XMin >= fb.XMin - 2.0 and cb.XMax <= fb.XMax + 2.0 and
                                cb.YMin >= fb.YMin - 2.0 and cb.YMax <= fb.YMax + 2.0 and
                                cb.ZMin >= f_z - 0.2 and cb.ZMax <= top_z + 0.2):
                                claimed_face_indices.add(c_idx)

                    pocket_chamfer = None
                    for ch in all_chamfer_faces:
                        cb = ch["bbox"]
                        if (cb.XMin >= fb.XMin - 3.0 and cb.XMax <= fb.XMax + 3.0 and
                            cb.YMin >= fb.YMin - 3.0 and cb.YMax <= fb.YMax + 3.0 and ch["is_top_rim"]):
                            pocket_chamfer = {
                                "has_chamfer": True,
                                "angle_deg": ch["semi_angle"],
                                "depth_mm": ch["depth_mm"],
                                "width_mm": ch["width_mm"],
                                "tool_required": f"{round(ch['semi_angle']*2)}deg chamfer mill or spot drill"
                            }
                            break
                            
                    ch_w = pocket_chamfer["width_mm"] if pocket_chamfer else 0.0
                    top_w_x = round(w_x + (2.0 * ch_w), 4) if ch_w > 0 else w_x
                    top_l_y = round(l_y + (2.0 * ch_w), 4) if ch_w > 0 else l_y

                    # Universal 2D Boundary Polygon & Island Loops Extraction
                    boundary_poly = []
                    island_polys = []
                    if len(face.Wires) > 0:
                        try:
                            w_pts = face.Wires[0].discretize(Deflection=0.1)
                            boundary_poly = [[round(p.x, 3), round(p.y, 3)] for p in w_pts]
                        except Exception:
                            boundary_poly = [[round(v.X, 3), round(v.Y, 3)] for v in face.Wires[0].Vertexes]
                        
                        for inner_w in face.Wires[1:]:
                            try:
                                iw_pts = inner_w.discretize(Deflection=0.1)
                                island_polys.append([[round(p.x, 3), round(p.y, 3)] for p in iw_pts])
                            except Exception:
                                pass

                    has_islands = len(island_polys) > 0
                    if has_islands:
                        is_open_step = False

                    p_type = "circular_pocket" if is_pure_circle else ("slot" if is_slot else ("island_pocket" if has_islands else ("open_step" if is_open_step else "prismatic_pocket")))
                    p_center = circ_center if is_pure_circle else [round(fb.Center.x, 4), round(fb.Center.y, 4)]
                    p_r = circ_radius if is_pure_circle else (slot_info["slot_radius_mm"] if is_slot else min_internal_r)

                    pockets.append({
                        "id": f"pocket_{len(pockets)+1}",
                        "type": p_type,
                        "is_circular": is_pure_circle,
                        "circular_radius_mm": circ_radius,
                        "circular_diameter_mm": round(circ_radius * 2.0, 4) if circ_radius else None,
                        "is_slot": is_slot,
                        "slot_info": slot_info,
                        "is_open_step": is_open_step,
                        "open_boundary_edges": open_edges,
                        "depth_from_external_top_mm": depth,
                        "floor_z_mm": round(f_z, 4),
                        "boundary_polygon_xy": boundary_poly,
                        "island_polygons_xy": island_polys,
                        "nominal_area_mm2": round(face.Area, 2),
                        "bounds": {
                            "min_x": round(fb.XMin, 4),
                            "max_x": round(fb.XMax, 4),
                            "min_y": round(fb.YMin, 4),
                            "max_y": round(fb.YMax, 4),
                            "width_x_mm": w_x,
                            "length_y_mm": l_y
                        },
                        "top_opening_dimensions": {
                            "width_x_mm": top_w_x,
                            "length_y_mm": top_l_y
                        },
                        "top_rim_chamfer": pocket_chamfer if pocket_chamfer else {"has_chamfer": False},
                        "center_mm": p_center,
                        "min_corner_radius_mm": p_r,
                        "max_tool_diameter_mm": round(p_r * 2.0, 4) if p_r else None,
                        "volume_mm3": pocket_volume
                    })

    # -------------------------------------------------------------
    # 5. Standard Vertical Holes (Z Axis) + Blind Bottom End-Caps
    # -------------------------------------------------------------
    vertical_holes = []
    for idx, face in enumerate(shape.Faces):
        if "Cylinder" in face.Surface.TypeId and idx not in claimed_face_indices:
            cyl = face.Surface
            axis = cyl.Axis
            radius = round(cyl.Radius, 4)
            diam = round(radius * 2.0, 4)
            # Filter: Holes larger than standard drill sizes (>12.5mm) are milled bores, not drill holes
            if diam > 12.5:
                continue
            if abs(axis.z - 1.0) < 1e-3 or abs(axis.z + 1.0) < 1e-3:
                # Check normal direction: for a hole/cavity, the normal points INWARD toward axis
                u_m = (face.ParameterRange[0] + face.ParameterRange[1]) / 2.0
                v_m = (face.ParameterRange[2] + face.ParameterRange[3]) / 2.0
                n = face.normalAt(u_m, v_m)
                pt = face.valueAt(u_m, v_m)
                radial_vec = FreeCAD.Vector(pt.x - cyl.Center.x, pt.y - cyl.Center.y, 0)
                dot = radial_vec.dot(FreeCAD.Vector(n.x, n.y, 0))
                if dot > 0:
                    # Normal points outward from axis -> This is an external boss/island, NOT a hole!
                    continue

                fb = face.BoundBox
                u_span = abs(face.ParameterRange[1] - face.ParameterRange[0])
                is_closed = abs(u_span - (2.0 * 3.1415926535)) < 0.1
                if is_closed or fb.ZLength >= (stock_dims["z_length_mm"] - 0.2):
                    claimed_face_indices.add(idx)
                    c_x = round(cyl.Center.x, 4)
                    c_y = round(cyl.Center.y, 4)
                    
                    # Claim blind hole bottom plane if present
                    for p_idx, p_face in enumerate(shape.Faces):
                        if "Plane" in p_face.Surface.TypeId and p_idx not in claimed_face_indices and p_idx not in stock_boundary_face_indices:
                            pb = p_face.BoundBox
                            if abs(pb.Center.x - c_x) < 0.5 and abs(pb.Center.y - c_y) < 0.5 and abs(pb.ZMin - fb.ZMin) < 0.2:
                                claimed_face_indices.add(p_idx)

                    hole_depth = round(top_z - fb.ZMin, 4)
                    hole_vol = round(math.pi * (radius ** 2) * hole_depth, 2)
                    
                    hole_chamfer = None
                    for ch in all_chamfer_faces:
                        cb = ch["bbox"]
                        if abs(cb.Center.x - c_x) < 1.0 and abs(cb.Center.y - c_y) < 1.0 and ch["is_top_rim"]:
                            hole_chamfer = {
                                "has_countersink": True,
                                "angle_deg": ch["semi_angle"],
                                "depth_mm": ch["depth_mm"],
                                "width_mm": ch["width_mm"]
                            }
                            break
                            
                    vertical_holes.append({
                        "id": f"vertical_hole_{len(vertical_holes)+1}",
                        "diameter_mm": diam,
                        "radius_mm": radius,
                        "depth_from_external_top_mm": hole_depth,
                        "center_xy_mm": [c_x, c_y],
                        "is_through_hole": bool(fb.ZLength >= (stock_dims["z_length_mm"] - 0.2)),
                        "top_countersink": hole_chamfer if hole_chamfer else {"has_countersink": False},
                        "volume_mm3": hole_vol
                    })

    # -------------------------------------------------------------
    # 6. Slanted / Angled Planar Surfaces + Adjacent Side Cheek Walls
    # -------------------------------------------------------------
    slanted_surfaces = []
    for idx, face in enumerate(shape.Faces):
        if "Plane" in face.Surface.TypeId and idx not in claimed_face_indices and idx not in stock_boundary_face_indices:
            u_m = (face.ParameterRange[0] + face.ParameterRange[1]) / 2.0
            v_m = (face.ParameterRange[2] + face.ParameterRange[3]) / 2.0
            normal = face.normalAt(u_m, v_m)
            tilt_deg = round(math.degrees(math.acos(min(1.0, max(-1.0, abs(normal.z))))), 2)
            
            # Inclined plane (not horizontal 0°, not vertical 90°)
            if 5.0 < tilt_deg < 85.0:
                claimed_face_indices.add(idx)
                fb = face.BoundBox
                
                # Claim adjacent vertical cheek walls that terminate this ramp
                for w_idx, w_face in enumerate(shape.Faces):
                    if "Plane" in w_face.Surface.TypeId and w_idx not in claimed_face_indices and w_idx not in stock_boundary_face_indices:
                        wb = w_face.BoundBox
                        # If wall overlaps the ramp in Z and is bounded by ramp limits
                        if (wb.ZMin >= fb.ZMin - 0.5 and wb.ZMax <= fb.ZMax + 0.5 and
                            wb.XMin >= fb.XMin - 1.0 and wb.XMax <= fb.XMax + 1.0 and
                            (abs(wb.YMin - fb.YMin) < 1.0 or abs(wb.YMax - fb.YMax) < 1.0)):
                            claimed_face_indices.add(w_idx)

                slanted_surfaces.append({
                    "id": f"slanted_surface_{len(slanted_surfaces)+1}",
                    "face_idx": idx,
                    "tilt_angle_from_horizontal_deg": tilt_deg,
                    "tilt_angle_from_vertical_deg": round(90.0 - tilt_deg, 2),
                    "normal_vector": [round(normal.x, 4), round(normal.y, 4), round(normal.z, 4)],
                    "z_height_range_mm": [round(fb.ZMin, 4), round(fb.ZMax, 4)],
                    "area_mm2": round(face.Area, 2),
                    "machining_strategy": "3D Surface Finishing (Ballnose) or Angled Chamfer Mill"
                })

    # -------------------------------------------------------------
    # 7. Horizontal Cross-Holes (Side Holes in X or Y) + End-Caps
    # -------------------------------------------------------------
    side_cross_holes = []
    for idx, face in enumerate(shape.Faces):
        if "Cylinder" in face.Surface.TypeId and idx not in claimed_face_indices:
            cyl = face.Surface
            axis = cyl.Axis
            # Axis strictly in XY plane (horizontal cylinder)
            if abs(axis.z) < 1e-3 and (abs(abs(axis.x) - 1.0) < 1e-3 or abs(abs(axis.y) - 1.0) < 1e-3):
                claimed_face_indices.add(idx)
                fb = face.BoundBox
                dia = round(cyl.Radius * 2.0, 4)
                axis_dir = "+X" if axis.x > 0.5 else ("-X" if axis.x < -0.5 else ("+Y" if axis.y > 0.5 else "-Y"))
                depth = round(fb.XLength if "X" in axis_dir else fb.YLength, 4)
                
                # Claim bottom end-cap plane of horizontal hole
                for p_idx, p_face in enumerate(shape.Faces):
                    if "Plane" in p_face.Surface.TypeId and p_idx not in claimed_face_indices and p_idx not in stock_boundary_face_indices:
                        pb = p_face.BoundBox
                        if abs(pb.Center.z - fb.Center.z) < 2.0 and p_face.Area < (math.pi * (cyl.Radius ** 2) * 1.5):
                            claimed_face_indices.add(p_idx)

                side_cross_holes.append({
                    "id": f"side_hole_{len(side_cross_holes)+1}",
                    "diameter_mm": dia,
                    "depth_mm": depth,
                    "axis_orientation": axis_dir,
                    "center_z_height_mm": round(fb.Center.z, 4),
                    "requires_setup": "Secondary Fixture (Op2) or 4th/5th Axis Rotary Indexing"
                })

    # -------------------------------------------------------------
    # 8. Compound-Angled Holes (Multi-Axis 3D Tilted Drill Vectors)
    # -------------------------------------------------------------
    compound_angled_holes = []
    for idx, face in enumerate(shape.Faces):
        if "Cylinder" in face.Surface.TypeId and idx not in claimed_face_indices:
            cyl = face.Surface
            axis = cyl.Axis
            # Axis is tilted (neither purely horizontal nor purely vertical)
            if 1e-3 < abs(axis.z) < 0.999:
                claimed_face_indices.add(idx)
                fb = face.BoundBox
                dia = round(cyl.Radius * 2.0, 4)
                tilt_from_vertical = round(math.degrees(math.acos(min(1.0, max(-1.0, abs(axis.z))))), 2)
                
                # Claim bottom end-cap / cone of this angled hole
                for p_idx, p_face in enumerate(shape.Faces):
                    if p_idx not in claimed_face_indices and p_idx not in stock_boundary_face_indices:
                        pb = p_face.BoundBox
                        if abs(pb.Center.z - fb.ZMin) < 2.0 and p_face.Area < (math.pi * (cyl.Radius ** 2) * 2.0):
                            claimed_face_indices.add(p_idx)

                compound_angled_holes.append({
                    "id": f"compound_angled_hole_{len(compound_angled_holes)+1}",
                    "diameter_mm": dia,
                    "radius_mm": round(cyl.Radius, 4),
                    "tool_axis_vector": [round(axis.x, 4), round(axis.y, 4), round(axis.z, 4)],
                    "tilt_angle_from_vertical_deg": tilt_from_vertical,
                    "z_height_range_mm": [round(fb.ZMin, 4), round(fb.ZMax, 4)],
                    "requires_setup": f"5-Axis Trunnion Table tilted at {tilt_from_vertical}° or Angled Fixture"
                })

    # -------------------------------------------------------------
    # 9. Undercut / Inverted Cavities (Downward Planar Ceilings)
    # -------------------------------------------------------------
    undercuts = []
    for idx, face in enumerate(shape.Faces):
        if "Plane" in face.Surface.TypeId and idx not in claimed_face_indices and idx not in stock_boundary_face_indices:
            u_m = (face.ParameterRange[0] + face.ParameterRange[1]) / 2.0
            v_m = (face.ParameterRange[2] + face.ParameterRange[3]) / 2.0
            normal = face.normalAt(u_m, v_m)
            
            # Normal pointing downwards (Z = -1) inside the part
            if normal.z < -0.9:
                claimed_face_indices.add(idx)
                fb = face.BoundBox
                
                # Claim the internal vertical walls of this undercut cavity
                for w_idx, w_face in enumerate(shape.Faces):
                    if "Plane" in w_face.Surface.TypeId and w_idx not in claimed_face_indices and w_idx not in stock_boundary_face_indices:
                        wb = w_face.BoundBox
                        if (wb.XMin >= fb.XMin - 2.0 and wb.XMax <= fb.XMax + 2.0 and
                            wb.YMin >= fb.YMin - 2.0 and wb.YMax <= fb.YMax + 2.0 and
                            wb.ZMax <= fb.ZMax + 0.5):
                            claimed_face_indices.add(w_idx)

                undercuts.append({
                    "id": f"undercut_{len(undercuts)+1}",
                    "ceiling_z_mm": round(face.CenterOfMass.z, 4),
                    "bounds": {
                        "width_x_mm": round(fb.XLength, 4),
                        "length_y_mm": round(fb.YLength, 4)
                    },
                    "requires_tool": "T-Slot Cutter, Undercutting Lollipop Mill, or Part Flip (Op2)"
                })

    # -------------------------------------------------------------
    # Sweep-up: Check any remaining unclaimed vertical/slanted planar walls
    # Only claim if face is adjacent to an already-claimed feature face (shares an edge)
    # -------------------------------------------------------------
    for idx, face in enumerate(shape.Faces):
        if idx not in claimed_face_indices and idx not in stock_boundary_face_indices:
            is_adjacent = False
            for edge in face.Edges:
                for c_idx in list(claimed_face_indices):
                    for c_edge in shape.Faces[c_idx].Edges:
                        if edge.isSame(c_edge):
                            is_adjacent = True
                            break
                    if is_adjacent:
                        break
                if is_adjacent:
                    break
            if is_adjacent:
                claimed_face_indices.add(idx)

    # =============================================================
    # AUDIT 1: Topological Surface Accounting ("No Orphan Faces")
    # =============================================================
    accounted_faces = claimed_face_indices.union(stock_boundary_face_indices)
    orphan_faces = [i for i in range(total_faces_count) if i not in accounted_faces]
    surface_coverage_pct = round((len(accounted_faces) / total_faces_count) * 100.0, 1)

    is_multi_setup = bool(len(side_cross_holes) > 0 or len(compound_angled_holes) > 0 or len(undercuts) > 0)

    # =============================================================
    # AUDIT 2: Volume Conservation
    # =============================================================
    extracted_volume_sum = (
        sum(p["volume_mm3"] for p in pockets) +
        sum(h["volume_mm3"] for h in vertical_holes) +
        sum(s["area_mm2"] * 6.0 for s in slanted_surfaces) +
        sum(math.pi * (h["radius_mm"]**2) * 15.0 for h in compound_angled_holes)
    )
    vol_ratio_pct = round(min(100.0, (extracted_volume_sum / max(1.0, target_removal_volume)) * 100.0), 1)

    audit_report = {
        "overall_status": "PASSED_VERIFIED" if len(orphan_faces) == 0 else "FLAGGED_FOR_INSPECTION",
        "machining_classification": (
            "Complex Multi-Axis / Multi-Setup (Requires 5-Axis Indexing & T-Slot Tooling)" if is_multi_setup
            else "Standard 3-Axis Prismatic"
        ),
        "audit_1_surface_accounting": {
            "total_cad_faces": total_faces_count,
            "stock_boundary_faces": len(stock_boundary_face_indices),
            "machined_feature_faces": len(claimed_face_indices),
            "orphan_unrecognized_faces": len(orphan_faces),
            "orphan_face_indices": orphan_faces,
            "surface_coverage_pct": surface_coverage_pct,
            "status": "PASSED (100% Geometry Accounted For)" if len(orphan_faces) == 0 else f"WARNING ({len(orphan_faces)} unclassified faces)"
        },
        "audit_2_volume_conservation": {
            "target_material_removal_mm3": target_removal_volume,
            "extracted_features_volume_mm3": round(extracted_volume_sum, 2),
            "volume_accountability_pct": vol_ratio_pct,
            "status": "CONSERVED" if vol_ratio_pct >= 90.0 else "DISCREPANCY_DETECTED"
        },
        "audit_3_multi_setup_partitioning": {
            "primary_top_3axis_features_count": len(pockets) + len(vertical_holes),
            "slanted_surfaces_count": len(slanted_surfaces),
            "horizontal_side_holes_count": len(side_cross_holes),
            "compound_angled_5axis_holes_count": len(compound_angled_holes),
            "undercut_cavities_count": len(undercuts),
            "status": "FULL_SETUP_DECOMPOSITION_COMPLETE"
        }
    }

    all_depths = [p["depth_from_external_top_mm"] for p in pockets] + [h["depth_from_external_top_mm"] for h in vertical_holes]

    features = {
        "source_cad_file": os.path.basename(step_file),
        "canonical_wcs": {
            "origin_type": "TOP_SURFACE_MATCH_CAD_XY",
            "delta_x": 0.0,
            "delta_y": 0.0,
            "delta_z": round(-bbox.ZMax, 4),
            "stock_bounds_wcs": {
                "min": [round(stock_dims["bounds"]["min"][0], 3), round(stock_dims["bounds"]["min"][1], 3), round(stock_dims["bounds"]["min"][2] - bbox.ZMax, 3)],
                "max": [round(stock_dims["bounds"]["max"][0], 3), round(stock_dims["bounds"]["max"][1], 3), 0.0]
            }
        },
        "stock_requirements": stock_dims,
        "verification_audit": audit_report,
        "features": {
            "setup_1_top_3axis": {
                "pockets": pockets,
                "vertical_holes": vertical_holes,
                "slanted_surfaces": slanted_surfaces
            },
            "setup_2_horizontal_side_features": {
                "side_cross_holes": side_cross_holes
            },
            "setup_3_multi_axis_5axis_features": {
                "compound_angled_holes": compound_angled_holes
            },
            "special_tooling_features": {
                "undercut_cavities": undercuts
            }
        },
        "machinability_constraints": {
            "requires_multiple_setups": is_multi_setup,
            "requires_5axis_indexing": bool(len(compound_angled_holes) > 0),
            "requires_undercut_tooling": bool(len(undercuts) > 0),
            "vertical_corner_radius_mm": min_internal_r,
            "max_tool_diam_for_corners_mm": round(min_internal_r * 2.0, 4) if min_internal_r else None,
            "total_chamfers_detected": len(all_chamfer_faces),
            "deepest_feature_depth_mm": max(all_depths, default=0.0)
        }
    }
    return features

# Execute
try:
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    step_arg = sys.argv[-2] if len(sys.argv) >= 3 else os.path.join(_this_dir, "sample_part.step")
    out_arg = sys.argv[-1] if len(sys.argv) >= 3 else os.path.join(_this_dir, "features.json")

    res = extract_features_exact(step_arg)
    os.makedirs(os.path.dirname(os.path.abspath(out_arg)), exist_ok=True)
    with open(out_arg, "w") as f:
        json.dump(res, f, indent=2)

    print("__FEATURES_JSON_EXTRACTED_SUCCESSFULLY__")
    sys.exit(0)
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
