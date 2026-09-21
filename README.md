# CNC-Agent: Autonomous Closed-Loop CNC Machining & Physical Verification Agent

An autonomous closed-loop CNC CAM agent that accepts arbitrary 3D CAD models (STEP) or 2D mechanical drawings (DXF), extracts topological features, plans multi-objective machining strategies on the Pareto Frontier, generates machine-ready G-code, simulates cut workpieces via CAMotics, physically verifies tolerances against the nominal CAD B-Rep using OpenCASCADE, and **iteratively self-corrects using diagnostic feedback until tolerances and safety converge**.

---

## Closed-Loop Agent Architecture

```
        Raw CAD Model (STEP / DXF)
                   │
                   ▼
       [1] Feature Extraction & Audit
                   │
    ┌──────────────┴──────────────────────────┐
    │                                         │
    │  Iterative Feedback Loop                │
    │  (up to N max iterations)               │
    │                                         │
    │  [2] LLM Strategy Planner (Gemini)      │ ◄─── Feedback Critique
    │             │                                  (scallops, cusps,
    │             ▼                                  chiploads, collisions)
    │  [3] Z-Level B-Rep Slicing G-Code       │           ▲
    │             │                                       │
    │             ▼                                       │
    │  [4] CAMotics Voxel Simulation          │           │
    │             │                                       │
    │             ▼                                       │
    │  [5] OpenCASCADE Surface Metrology      │           │
    │             │                                       │
    │             ▼                                       │
    │  [5b] Diagnostic Critique Evaluator ────┴───────────┘
    │             │ (Converged: 0 collisions, tolerances met)
    │             ▼
    └──────► [6] Interactive Pareto Report & Convergence History
```

---

## Key Features

1. **Closed-Loop Feedback & Convergence**:
   - Compares physical simulation and metrology against strict criteria: zero rapid collisions, zero gouges, safe chiploads, and target surface finish.
   - Diagnoses root causes and calculates mathematical adjustments (e.g. required stepover for floor scallop $s = 2\sqrt{2Rh - h^2}$, chipload-safe feedrates, corner tool reach).
   - Re-prompts Gemini (with deterministic programmatic fallback) to revise CAM strategies across iterations (`iter_1/`, `iter_2/`) until convergence.

2. **Deterministic B-Rep & Drawing Analysis**:
   - Automated pocket, profile, and hole extraction directly from STEP B-Rep topology using FreeCAD/OpenCASCADE.
   - Normal vector auditing to eliminate inverted holes and false detections.
   - Automatic bounding box computation for optimal stock sizing.

3. **Multi-Objective Strategy Planning**:
   - Formulates three distinct strategies across the Pareto Frontier:
     - `CYCLE_TIME`: Aggressive roughing, maximal tool engagement, high MRR.
     - `ACCURACY_TUNED`: Fine stepovers, conservative stepdowns, dedicated finishing passes.
     - `BALANCED`: Industrial trade-off balancing tool life, cycle time, and surface finish.

4. **General Z-Level B-Rep Toolpath Slicer**:
   - Cross-section slicing across arbitrary geometry: handles complex stepped pockets, bosses, islands, and open contours.
   - Multi-contour 2D polygon offsetting via Shapely.
   - Clean helical/ramp entries and retract clearances.

5. **Physical Simulation (CAMotics)**:
   - Headless voxel cutting simulation via `camsim`.
   - Rapid-traverse (G00) collision detection below stock surface.
   - Kinematic cycle time calculation factoring in acceleration, tool changes, and dwells.
   - Export of simulated cut workpiece meshes (.stl).

6. **OpenCASCADE Metrological Audit**:
   - Euclidean distance point-projection against nominal CAD B-Rep surfaces.
   - Calculation of mean and max surface deviation (µm), corner cusp residual, and volumetric removal.
   - Zero-gouge safety assertion.

7. **Interactive Visual Reporting**:
   - Standalone dark-mode HTML executive report with Pareto trade-off charts, closed-loop convergence timeline, and toolpath statistics.

---

## Directory Structure

```
cnc-agent/
├── pyproject.toml              # Package configuration & console entrypoint
├── README.md                   # Documentation
├── run_pipeline.py             # Master closed-loop orchestrator CLI
├── 01_feature_extractor.py     # Stage 1: STEP / DXF feature extraction
├── _extract_worker.py          # FreeCAD worker for B-Rep topology inspection
├── 02_llm_planner.py           # Stage 2: Gemini strategy planner (feedback-aware)
├── 03_toolpath_generator.py    # Stage 3: Z-level B-Rep toolpath generator
├── _slice_worker.py            # FreeCAD worker for cross-section slicing
├── 04_camotics_verifier.py     # Stage 4: CAMotics simulation & safety audit
├── 05_surface_comparator.py    # Stage 5: Nominal CAD vs. cut mesh comparator
├── _surface_worker.py          # FreeCAD worker for OpenCASCADE surface projection
├── 05b_critique_evaluator.py   # Stage 5b: Diagnostic critique & convergence engine
├── 06_report_generator.py      # Stage 6: HTML report with convergence timeline
├── tool_library.json           # Standard CNC tooling catalog
├── .env                        # Environment variables (GEMINI_API_KEY)
├── schemas/                    # Pydantic / JSON validation schemas
├── step/                       # Test CAD models
└── runs/                       # Isolated outputs per pipeline run
    └── run_<id>/
        ├── iter_1/             # Iteration 1 simulation, G-code, critique
        ├── iter_2/             # Iteration 2 revised simulation & metrology
        ├── iteration_history.json # Convergence progression log
        ├── source_cad.step
        ├── tool_library.json
        ├── features.json
        ├── strategies.json
        ├── 1_cycle_time.ngc
        ├── 2_accuracy_tuned.ngc
        ├── 3_balanced.ngc
        ├── 1_cycle_time.camotics
        ├── 1_cycle_time_cut.stl
        ├── simulation_results.json
        ├── deviations.json
        ├── critique.json
        ├── frontier_report.html
        └── run_metadata.json
```

---

## Prerequisites

1. **Astral `uv`** (Python package & environment manager):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **FreeCAD 1.0+** CLI (`freecadcmd`):
   ```bash
   sudo apt-get install freecad
   # Verifies FreeCAD B-Rep geometry kernel is available in PATH
   ```
3. **CAMotics** CLI (`camsim`):
   ```bash
   sudo apt-get install camotics
   # Verifies voxel cutting simulation engine is available in PATH
   ```
4. **Gemini API Key** *(Optional: deterministic physics planner activates automatically if omitted)*:
   ```bash
   export GEMINI_API_KEY="your-api-key"
   # Or configure in .env (see .env.example)
   ```

---

## Installation & Setup

Setup is completely automated with `uv`:

```bash
# 1. Clone the repository
git clone https://github.com/maniradhakrishnan-dev/cnc-agent.git
cd cnc-agent

# 2. (Optional) Create environment file for LLM planner
cp .env.example .env

# 3. Synchronize virtual environment & all dependencies
uv sync
```

---

## Pre-Flight System Doctor

Run the built-in system doctor to verify your Python environment, CAD/CAM binaries, and computational geometry libraries in 1 second:

```bash
uv run cnc-agent --doctor
```

Expected output:
```text
===========================================================================
 🩺 CNC AGENT PRE-FLIGHT SYSTEM DOCTOR
===========================================================================
 [✓] Python Version     : 3.11.x (Required: >= 3.11)
 [✓] Package Manager    : uv
 [✓] CAD Kernel Engine  : FreeCAD 1.x (freecadcmd)
 [✓] Voxel Simulator    : camsim
 [✓] Library: pydantic  : v2.x (Pydantic v2 Models)
 [✓] Library: shapely   : v2.x (2D Computational Geometry)
 [✓] Library: trimesh   : v5.x (3D Mesh Processing)
 [✓] Library: numpy     : v2.x (Numerical Computing)
 [✓] Library: ezdxf     : v1.x (DXF CAD Parser)
===========================================================================
 [STATUS: SYSTEM READY] All critical dependencies are verified.
===========================================================================
```

---

## Verification & Testing Suite

Run the automated test suite matching all specifications from `REQ.md`:

```bash
uv run python3 -m unittest discover tests
```

Tests included:
- **The Refusal Case** (`tests/test_refusal_case.py`): Ensures parts with internal corner radii smaller than minimum tool diameter are rejected with a formal `RefusalNotice` rather than producing gouging toolpaths.
- **Prediction vs. Result Gap Analysis** (`tests/test_prediction_gap.py`): Validates cycle time, scallop height, mean deviation, and chipload gap calculations.
- **Mutation Corpus** (`tests/test_mutation_corpus.py`): Verifies physical/kinematic checker catches all 4 planted defects (rapid crash into stock, tool longer than machine Z travel, stepdown deeper than flute length, skipped finishing face).
- **2D DXF Pipeline** (`tests/test_dxf_pipeline.py`): Validates 2D vector drawing ingestion, feature extraction, and strategy planning.

---

## Quickstart: Running the Agent

### 1. Execute on 3D CAD Drawing (STEP)
```bash
uv run cnc-agent step/01_simple_holes_plate.step
```

### 2. Execute on 2D Mechanical Drawing (DXF)
```bash
uv run cnc-agent sample_part.dxf
```

### 3. Customize Iteration Limits and Tolerances
```bash
uv run cnc-agent step/machining_block_03.step \
    --run-id block03_prod \
    --max-iterations 3 \
    --target-accuracy-scallop-um 35.0 \
    --target-balanced-scallop-um 75.0
```

---

## Inspecting Outputs

All run artifacts are saved under `runs/<run_id>/`:

- **Interactive HTML Pareto Report**:
  ```bash
  xdg-open runs/<run_id>/frontier_report.html
  ```
- **3D Cut Simulation** (workpiece voxel removal):
  ```bash
  camotics runs/<run_id>/1_cycle_time.camotics &
  ```
- **Machine-Ready G-Code**:
  `runs/<run_id>/1_cycle_time.ngc`, `2_accuracy_tuned.ngc`, `3_balanced.ngc`
- **Convergence History & Critique**:
  `runs/<run_id>/iteration_history.json`, `runs/<run_id>/critique.json`
