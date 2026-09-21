# CNC-MC-Agent: Autonomous Closed-Loop CNC Machine Code (G-Code) Generation, Simulation & Verification

> **"A drawing goes in. Several complete, machine-ready programs come out, on a measured accuracy-versus-time frontier."** — *REQ.md*

**CNC-MC-Agent** is an autonomous CAM agent that **Generates**, **Simulates**, and **Physically Verifies** CNC Machine Code (G-code) directly from 3D CAD models (STEP) and 2D drawings (DXF). 

Instead of generating G-code blindly, the agent operates in a **closed-loop feedback cycle**: it plans multi-objective strategies across the Pareto Frontier, executes voxel cutting physics, audits the simulated result against the nominal engineering model, and **autonomously self-corrects the machine code until safety and tolerances converge**.

---

## The Three Core Pillars of CNC-MC-Agent

```
   ┌─────────────────────────────────────────────────────────────────────────────────────────┐
   │                                                                                         │
   │   1. GENERATE                      2. SIMULATE                    3. VERIFY             │
   │   Machine Code (G-Code)            Machine Code Execution         Physical & Metrology  │
   │                                                                                         │
   │   • STEP / DXF Feature Extraction  • Headless Voxel Cutting       • Zero Rapid Crash    │
   │   • Pareto Multi-Objective Plan      (CAMotics camsim)              (G00 into Stock)    │
   │     - CYCLE_TIME (High MRR)        • Line-by-line G-code          • Bed Strike Check    │
   │     - ACCURACY_TUNED (Fine finish)   kinematic run-time             (Machine table)     │
   │     - BALANCED (Tool life/finish)  • True machine cycle time      • Flute Stepdown      │
   │   • Z-Level B-Rep Slicing          • Cut workpiece mesh export      vs Shank Rubbing    │
   │   • Clean Helical Ramp Entries       (.stl output)                • Sub-micron CAD      │
   │   • Machine-Ready G-code (.ngc)                                     surface metrology   │
   │                                                                   • Zero-gouge proof    │
   │                                                                                         │
   └────────────────────────────────────────────┬────────────────────────────────────────────┘
                                                │
                                 Closed-Loop Feedback Loop
                         (Diagnoses errors ──► Re-plans G-code)
```

---

## Closed-Loop Agent Architecture

```
         Raw CAD Model (STEP / DXF) + Tool Library
                             │
                             ▼
              [1] Feature Extraction & Audit
              (OpenCASCADE B-Rep / ezdxf 2D)
                             │
     ┌───────────────────────┴─────────────────────────┐
     │                                                 │
     │  Iterative Self-Correction Loop                 │
     │  (up to N max iterations)                       │
     │                                                 │
     │  [2] GENERATE: Strategy Planner (Gemini)        │ ◄─── Diagnostic Critique
     │         │                                                (exact math: stepovers,
     │         ▼                                                feeds, flute reach)
     │  [3] GENERATE: Z-Level Slicing Toolpaths        │                 ▲
     │         │                                                         │
     │         ▼                                                         │
     │  [4] SIMULATE: CAMotics Voxel Physics           │                 │
     │         │ (camsim G-code cutting simulation)                      │
     │         ▼                                                         │
     │  [5] VERIFY: OpenCASCADE Surface Metrology      │                 │
     │         │ (Nominal CAD vs. Cut Workpiece Mesh)                    │
     │         ▼                                                         │
     │  [5b] VERIFY: Diagnostic Critique Evaluator ────┴─────────────────┘
     │         │ (Passed: 0 collisions, 0 gouges, tolerances met)
     │         ▼
     └─► [6] Standalone Interactive Pareto Report & Convergence Timeline
```

---

## 1. Machine Code Generation
- **Automated Feature Extraction**: Parses STEP solid topology (cylinders, pockets, planar floors, outer bounds) and 2D DXF vector layers.
- **Pareto Multi-Objective Planning**: Produces three distinct G-code programs on the accuracy-vs-time frontier:
  - `CYCLE_TIME`: Maximal radial/axial engagement, high MRR, aggressive feeds.
  - `ACCURACY_TUNED`: Fine stepovers ($< 20\%$), conservative stepdowns, dedicated finishing passes.
  - `BALANCED`: Industrial compromise balancing tool life, cycle time, and surface finish.
- **Cross-Section Toolpath Slicing**: Boolean subtraction (`Stock - CAD Solid`) sliced into 2D planar contours, offset using Shapely, with clean helical plunges and retract clearances.
- **Machine Dialect**: Outputs clean, standardized LinuxCNC / GRBL G-code (`.ngc`).

## 2. Machine Code Simulation
- **Voxel Cutting Physics**: Spawns CAMotics (`camsim`) headlessly to simulate spinning cylindrical and ball cutters carving material out of the raw stock block line-by-line.
- **Kinematic Machining Time**: Calculates realistic machine execution time factoring in feed rates, rapid traverses, tool change penalties, and dwells.
- **Workpiece Mesh Generation**: Exports the final cut workpiece as a 3D surface mesh (`.stl`) for geometric inspection.

## 3. Physical & Metrological Verification
- **Rapid Traverse Crash Detection**: Traps any `G00` motion plunging or moving laterally below stock level ($Z \le 0$).
- **Machine Bed & Workbench Collision**: Asserts that tools never cut past the stock bottom into the machine bed, vise, or fixture.
- **Axis Overtravel**: Checks all commanded $X, Y, Z$ positions against physical machine travel limits.
- **Shank Friction / Flute Length**: Asserts that axial stepdowns never exceed cutting flute lengths ($\Delta Z \le L_{\text{flute}}$).
- **Surface Metrology in Microns**: OpenCASCADE projects simulated cut mesh vertices against the original CAD NURBS surfaces, computing mean/max deviation in $\mu\text{m}$ and asserting **zero gouging**.
- **The Refusal Case (REQ.md Line 41)**: If an internal pocket corner radius is smaller than the smallest tool in the library ($R_{\min} < R_{\text{tool}}$), the agent safely halts and outputs a formal `refusal_notice.json` naming the required tool diameter rather than generating gouging machine code.

---

## Directory Structure

```
cnc-mc-agent/
├── pyproject.toml              # Package configuration & console entrypoints
├── README.md                   # Documentation
├── run_pipeline.py             # Master closed-loop orchestrator CLI
├── tool_library.json           # Standard CNC tooling catalog
├── .env.example                # Template for environment variables (GEMINI_API_KEY)
├── sample_part.step & .dxf     # Benchmark test parts
├── schemas/                    # Pydantic v2 validation models & JSON schemas
│   ├── models.py
│   ├── features.schema.json
│   └── verification.schema.json
├── tests/                      # Automated test suite (unit, mutation, refusal, DXF)
│   ├── test_refusal_case.py
│   ├── test_mutation_corpus.py
│   ├── test_prediction_gap.py
│   └── test_dxf_pipeline.py
├── step/                       # Benchmark CAD models (plates, pockets, islands)
├── core/                       # Core Agent Engine & Verification Stages
│   ├── __init__.py             # Package exports
│   ├── feature_extractor.py    # Stage 1: STEP / DXF feature extraction
│   ├── _extract_worker.py      # FreeCAD worker for B-Rep topology inspection
│   ├── llm_planner.py          # Stage 2: Gemini strategy planner (feedback-aware)
│   ├── toolpath_generator.py   # Stage 3: Z-level B-Rep toolpath generator
│   ├── _slice_worker.py        # FreeCAD worker for cross-section slicing
│   ├── camotics_verifier.py    # Stage 4: CAMotics simulation & safety audit
│   ├── surface_comparator.py   # Stage 5: Nominal CAD vs. cut mesh comparator
│   ├── _surface_worker.py      # FreeCAD worker for OpenCASCADE surface projection
│   ├── critique_evaluator.py   # Stage 5b: Diagnostic critique & convergence engine
│   └── report_generator.py     # Stage 6: HTML report with convergence timeline
└── runs/                       # Isolated outputs per pipeline run
    └── run_<id>/
        ├── iter_1/             # Iteration 1 simulation, G-code, critique
        ├── iter_2/             # Iteration 2 revised simulation & metrology
        ├── iteration_history.json # Convergence progression log
        ├── 1_cycle_time.ngc    # G-code (Cycle Time)
        ├── 2_accuracy_tuned.ngc# G-code (Accuracy Tuned)
        ├── 3_balanced.ngc      # G-code (Balanced)
        ├── 1_cycle_time.camotics # 3D visual simulation
        ├── simulation_results.json
        ├── deviations.json
        ├── critique.json
        └── frontier_report.html
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
   # Provides the OpenCASCADE B-Rep CAD geometry kernel
   ```
3. **CAMotics** CLI (`camsim`):
   ```bash
   sudo apt-get install camotics
   # Provides the voxel cutting simulation engine
   ```
4. **Gemini API Key** *(Optional: deterministic physics planner activates automatically if omitted)*:
   ```bash
   export GEMINI_API_KEY="your-api-key"
   # Or configure in .env (see .env.example)
   ```

---

## Installation & Setup

Setup is completely automated via `uv`:

```bash
# 1. Clone the repository
git clone https://github.com/maniradhakrishnan-dev/cnc-mc-agent.git
cd cnc-mc-agent

# 2. (Optional) Configure environment file for LLM planner
cp .env.example .env

# 3. Synchronize virtual environment & all dependencies
uv sync
```

---

## Pre-Flight System Doctor

Verify your Python environment, CAD/CAM binaries, and computational geometry libraries in 1 second:

```bash
uv run cnc-mc-agent --doctor
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
- **The Refusal Case** (`tests/test_refusal_case.py`): Rejects unmachinable internal corners with formal `RefusalNotice`.
- **Prediction vs. Result Gap Analysis** (`tests/test_prediction_gap.py`): Validates cycle time, scallop height, mean deviation, and chipload gap calculations.
- **Mutation Corpus** (`tests/test_mutation_corpus.py`): Validates detection of planted rapid collisions, axis overtravel, bed strikes, over-flute stepdowns, and skipped finishing faces (6 mutations).
- **2D DXF Pipeline** (`tests/test_dxf_pipeline.py`): Validates 2D vector drawing ingestion and G-code generation.

---

## Quickstart: Running the Agent

### 1. Generate & Verify Machine Code for 3D CAD Part (STEP)
```bash
uv run cnc-mc-agent step/01_simple_holes_plate.step
```

### 2. Generate & Verify Machine Code for 2D Drawing (DXF)
```bash
uv run cnc-mc-agent sample_part.dxf
```

### 3. Customize Iteration Limits and Surface Finish Tolerances
```bash
uv run cnc-mc-agent step/machining_block_03.step \
    --run-id block03_prod \
    --max-iterations 3 \
    --target-accuracy-scallop-um 35.0 \
    --target-balanced-scallop-um 75.0
```

---

## Inspecting Generated Outputs

All run artifacts are saved under `runs/<run_id>/`:

- **Interactive HTML Pareto Report**:
  ```bash
  xdg-open runs/<run_id>/frontier_report.html
  ```
- **3D Cut Simulation** (workpiece voxel removal):
  ```bash
  camotics runs/<run_id>/1_cycle_time.camotics &
  ```
- **Machine-Ready G-Code Programs**:
  - `runs/<run_id>/1_cycle_time.ngc` (High material removal rate)
  - `runs/<run_id>/2_accuracy_tuned.ngc` (Fine surface finish)
  - `runs/<run_id>/3_balanced.ngc` (Industrial balanced trade-off)
- **Convergence History & Critique**:
  `runs/<run_id>/iteration_history.json`, `runs/<run_id>/critique.json`
