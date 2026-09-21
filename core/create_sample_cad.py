import sys, os
import FreeCAD, Part

out_dir = os.path.dirname(os.path.abspath(__file__))
os.makedirs(out_dir, exist_ok=True)

# 1. Base block: 100 x 80 x 10 mm
base = Part.makeBox(100.0, 80.0, 10.0)

# 2. Pocket with filleted corners: 50 x 40 x 4 mm located at center (25, 20, 6)
pocket_box = Part.makeBox(50.0, 40.0, 4.0, FreeCAD.Vector(25.0, 20.0, 6.0))
# Fillet vertical edges (R=3.0 mm)
pocket_v_edges = [e for e in pocket_box.Edges if abs(e.Length - 4.0) < 1e-3 and abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 3.9]
filleted_pocket = pocket_box.makeFillet(3.0, pocket_v_edges)

# Cut pocket out of base
part = base.cut(filleted_pocket)

# 3. Four corner through-holes: diam 6mm (R=3mm), depth 10mm
hole_coords = [(12.0, 12.0), (88.0, 12.0), (88.0, 68.0), (12.0, 68.0)]
for hx, hy in hole_coords:
    hole = Part.makeCylinder(3.0, 10.0, FreeCAD.Vector(hx, hy, 0.0), FreeCAD.Vector(0.0, 0.0, 1.0))
    part = part.cut(hole)

step_path = os.path.join(out_dir, "sample_part.step")
part.exportStep(step_path)
print(f"SUCCESS: Created sample STEP file at: {step_path}")
sys.exit(0)
