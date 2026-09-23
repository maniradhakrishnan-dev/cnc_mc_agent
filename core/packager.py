"""Production Packager & Shop Floor Setup Sheet Generator.

Packages all verified artifacts from a CNC agent run into a portable
distribution bundle (.zip) ready for machine shop dispatch:
- G-Code (.ngc) files for all Pareto strategies
- Visual Interactive HTML Inspection Report
- Tooling Setup Sheet (Markdown & HTML) with WCS, speeds, feeds, tool numbers
- 3D Verification Cut Mesh (.stl)
- Source CAD archive (.step / .dxf)
"""

import os
import json
import zipfile
from datetime import datetime
from pathlib import Path


def generate_setup_sheet(run_dir: str) -> str:
    """Generate a clean Markdown and HTML Setup Sheet for CNC machinists."""
    features_path = os.path.join(run_dir, "features.json")
    tools_path = os.path.join(run_dir, "tool_library.json")
    strategies_path = os.path.join(run_dir, "strategies.json")
    metadata_path = os.path.join(run_dir, "run_metadata.json")
    deviations_path = os.path.join(run_dir, "deviations.json")

    features = {}
    if os.path.exists(features_path):
        with open(features_path) as f:
            features = json.load(f)

    tools_data = {}
    if os.path.exists(tools_path):
        with open(tools_path) as f:
            tools_data = json.load(f)

    strategies = {}
    if os.path.exists(strategies_path):
        with open(strategies_path) as f:
            strategies = json.load(f).get("strategies", {})

    metadata = {}
    if os.path.exists(metadata_path):
        with open(metadata_path) as f:
            metadata = json.load(f)

    deviations = {}
    if os.path.exists(deviations_path):
        with open(deviations_path) as f:
            deviations = json.load(f)

    run_id = metadata.get("run_id", os.path.basename(run_dir))
    cad_name = os.path.basename(metadata.get("source_cad_file", "part.step"))
    stock = features.get("stock_requirements", {})
    sx = stock.get("x_length_mm", 100.0)
    sy = stock.get("y_length_mm", 80.0)
    sz = stock.get("z_length_mm", 15.0)

    # Tool mapping
    tools_map = {t.get("tool_number", t.get("number", i+1)): t for i, t in enumerate(tools_data.get("tools", []))}

    sheet = f"""# CNC MACHINING SETUP & OPERATOR SHEET

**Run Identifier:** `{run_id}`  
**Source CAD Model:** `{cad_name}`  
**Generated Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Verification Status:** {'VERIFIED CONVERGED' if metadata.get('converged') else 'COMPLETED MAX ITERATIONS'}  

---

## 1. Workpiece & Raw Stock Billet
- **Dimensions (X × Y × Z):** `{sx:.2f} mm × {sy:.2f} mm × {sz:.2f} mm`
- **Recommended Material:** 6061-T6 Aluminum Billet
- **Work Coordinate System (WCS):** `G54`
- **Part Zero Datum:** Top Face Center (X=0, Y=0, Z=0 at stock top center)
- **Safe Clearance Plane:** Z = +25.000 mm

---

## 2. Tooling Carousel Setup
| Tool # | Tool Description | Type | Diameter | Flute Length | Min Flute Reach |
| :---: | :--- | :---: | :---: | :---: | :---: |
"""

    for t_num, t in sorted(tools_map.items()):
        sheet += f"| **T{t_num:02d}** | {t.get('name', 'Tool')} | {t.get('shape', 'cylindrical').capitalize()} | {t.get('diameter_mm', 0):.1f} mm | {t.get('flute_length_mm', 0):.1f} mm | {t.get('total_length_mm', 0):.1f} mm |\n"

    sheet += """
---

## 3. Production Programs (Pareto Frontier)

| Program File | Strategy Tradeoff | Est. Cycle Time | Target Scallop | Verified Status |
| :--- | :--- | :---: | :---: | :---: |
"""
    for strat_key, strat in strategies.items():
        dev_info = deviations.get(strat_key, {})
        fname = f"1_cycle_time.ngc" if "CYCLE" in strat_key else (f"2_accuracy_tuned.ngc" if "ACCURACY" in strat_key else f"3_balanced.ngc")
        time_str = dev_info.get("cycle_time_formatted", "N/A")
        scallop_str = f"{dev_info.get('floor_scallop_height_um', 0.0):.1f} µm"
        verif_str = dev_info.get("gouging_check", {}).get("status", "UNCHECKED")
        sheet += f"| `{fname}` | **{strat.get('name', strat_key)}** | {time_str} | {scallop_str} | {verif_str} |\n"

    sheet += """
---

## 4. Pre-Machining Checklist
- [ ] Raw stock clamped securely in vise with parallels supporting underneath.
- [ ] Probe X and Y to find stock center (set G54 X0 Y0).
- [ ] Touch off tool lengths on top surface (set G54 Z0).
- [ ] Flood coolant nozzles aimed at cutting zone (M08 enabled in program).
- [ ] Dry run single-block test on first clearance pass at Z = +25.000 mm.
"""
    setup_path = os.path.join(run_dir, "SETUP_SHEET.md")
    with open(setup_path, "w") as f:
        f.write(sheet)
    return setup_path


def package_production_bundle(run_dir: str) -> str:
    """Bundle all necessary production files into a clean .zip for shop floor dispatch."""
    setup_sheet_path = generate_setup_sheet(run_dir)
    run_id = os.path.basename(os.path.abspath(run_dir))
    zip_path = os.path.join(run_dir, f"{run_id}_production_package.zip")

    files_to_pack = [
        "SETUP_SHEET.md",
        "frontier_report.html",
        "1_cycle_time.ngc",
        "2_accuracy_tuned.ngc",
        "3_balanced.ngc",
        "1_cycle_time_cut.stl",
        "2_accuracy_tuned_cut.stl",
        "3_balanced_cut.stl",
        "1_cycle_time.camotics",
        "2_accuracy_tuned.camotics",
        "3_balanced.camotics",
        "features.json",
        "deviations.json",
        "run_metadata.json",
        "source_cad.step",
        "source_cad.dxf"
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for fname in files_to_pack:
            fpath = os.path.join(run_dir, fname)
            if os.path.exists(fpath):
                zipf.write(fpath, arcname=fname)

    return zip_path
