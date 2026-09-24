"""CNC Machine Code Agent - Interactive Web Application.

A production-grade web dashboard and microservice:
- Drag-and-drop CAD file uploader (.step, .stp, .dxf)
- 1-click Benchmark Part Selector (PART_01 to PART_08)
- Real-time Server-Sent Events (SSE) live execution terminal log
- Embedded Interactive Pareto Frontier Report
- One-click Production Bundle (.zip) and Setup Sheet download

Run locally:
    uv run python web_app.py
    # or: uvicorn web_app:app --host 0.0.0.0 --port 8000
"""

import os
import sys
import uuid
import json
import asyncio
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

# Paths
AGENT_DIR = Path(__file__).resolve().parent
RUNS_DIR = AGENT_DIR / "runs"
INPUT_DIR = AGENT_DIR / "input_files" / "step"
CORE_DIR = AGENT_DIR / "core"

RUNS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="CNC Machine Code Agent",
    description="Physical CAD/CAM Verification & Machine-Code Generation Microservice",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active execution registry in memory: {run_id: {"process": proc, "lines": [], "status": str}}
active_runs = {}


def get_available_benchmark_parts():
    """List all available benchmark STEP files."""
    parts = []
    if INPUT_DIR.exists():
        for f in sorted(INPUT_DIR.glob("*.step")):
            parts.append({
                "filename": f.name,
                "path": str(f),
                "name": f.stem.replace("_", " ").title()
            })
    sample = AGENT_DIR / "sample_part.step"
    if sample.exists():
        parts.insert(0, {
            "filename": "sample_part.step",
            "path": str(sample),
            "name": "Sample Part (Standard Prismatic Benchmark)"
        })
    return parts


@app.get("/api/benchmarks")
async def list_benchmarks():
    return get_available_benchmark_parts()


@app.post("/api/run")
async def start_run(
    cad_file: Optional[UploadFile] = File(None),
    benchmark_name: Optional[str] = Form(None),
    max_iterations: int = Form(1),
):
    """Start an autonomous pipeline execution asynchronously."""
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    target_cad_path = None

    if cad_file and cad_file.filename:
        safe_filename = Path(cad_file.filename).name
        target_cad_path = run_dir / safe_filename
        with open(target_cad_path, "wb") as buffer:
            shutil.copyfileobj(cad_file.file, buffer)
    elif benchmark_name:
        candidate = INPUT_DIR / benchmark_name
        if not candidate.exists() and benchmark_name == "sample_part.step":
            candidate = AGENT_DIR / "sample_part.step"
        if candidate.exists():
            target_cad_path = candidate
        else:
            raise HTTPException(status_code=400, detail=f"Benchmark part '{benchmark_name}' not found.")
    else:
        # Default fallback to sample_part.step
        candidate = AGENT_DIR / "sample_part.step"
        if candidate.exists():
            target_cad_path = candidate
        else:
            raise HTTPException(status_code=400, detail="No CAD file provided or selected.")

    # Initialize run registry entry
    active_runs[run_id] = {
        "run_id": run_id,
        "cad_file": str(target_cad_path),
        "run_dir": str(run_dir),
        "status": "running",
        "lines": [],
        "returncode": None,
        "summary": None,
    }

    # Launch subprocess asynchronously
    asyncio.create_task(_execute_pipeline(run_id, str(target_cad_path), run_dir, max_iterations))

    return {"status": "started", "run_id": run_id}


async def _execute_pipeline(run_id: str, cad_path: str, run_dir: Path, max_iterations: int):
    """Execute run_pipeline.py as an async subprocess and capture stdout line-by-line."""
    state = active_runs[run_id]
    state["lines"].append(f"[WEB SERVICE] Initializing Autonomous Run: {run_id}\n")
    state["lines"].append(f"[WEB SERVICE] Target CAD Model: {Path(cad_path).name}\n")
    state["lines"].append(f"[WEB SERVICE] Max Feedback Iterations: {max_iterations}\n")
    state["lines"].append("=" * 80 + "\n")

    cmd = [
        sys.executable,
        str(AGENT_DIR / "run_pipeline.py"),
        "--cad", cad_path,
        "--run-id", run_id,
        "--max-iterations", str(max_iterations)
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(AGENT_DIR)
        )
        state["process"] = proc

        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace")
            state["lines"].append(decoded)

        await proc.wait()
        state["returncode"] = proc.returncode

        if proc.returncode == 0:
            state["status"] = "completed"
            state["lines"].append("\n[✓] PIPELINE EXECUTION FINISHED SUCCESSFULLY.\n")
        elif proc.returncode == 2:
            state["status"] = "refused"
            state["lines"].append("\n[🛑] FORMAL MACHINABILITY REFUSAL ISSUED (REQ.md Constraint).\n")
        else:
            state["status"] = "error"
            state["lines"].append(f"\n[❌] PIPELINE EXITED WITH CODE {proc.returncode}.\n")

        # Load metadata/summary if present
        meta_file = run_dir / "run_metadata.json"
        if meta_file.exists():
            with open(meta_file) as f:
                state["summary"] = json.load(f)

    except Exception as e:
        state["status"] = "error"
        state["lines"].append(f"\n[❌] Server exception during execution: {str(e)}\n")


@app.get("/api/stream/{run_id}")
async def stream_logs(run_id: str):
    """Server-Sent Events (SSE) streaming live stdout lines to the browser."""
    if run_id not in active_runs:
        raise HTTPException(status_code=404, detail="Run ID not found.")

    async def event_generator():
        last_idx = 0
        while True:
            state = active_runs.get(run_id)
            if not state:
                break
            lines = state["lines"]
            while last_idx < len(lines):
                line_data = lines[last_idx]
                yield {"data": line_data}
                last_idx += 1

            if state["status"] in ("completed", "refused", "error"):
                yield {"data": f"__STATUS_FINISHED__:{state['status']}"}
                break
            await asyncio.sleep(0.08)

    return EventSourceResponse(event_generator())


@app.get("/api/status/{run_id}")
async def get_status(run_id: str):
    """Get JSON summary and artifact availability for a given run."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found.")

    state = active_runs.get(run_id, {})
    meta_path = run_dir / "run_metadata.json"
    meta = None
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)

    refusal_path = run_dir / "refusal_notice.json"
    refusal = None
    if refusal_path.exists():
        with open(refusal_path) as f:
            refusal = json.load(f)

    report_path = run_dir / "frontier_report.html"
    pkg_path = run_dir / f"{run_id}_production_package.zip"

    return {
        "run_id": run_id,
        "status": state.get("status", "completed" if meta else "unknown"),
        "has_report": report_path.exists(),
        "has_package": pkg_path.exists(),
        "metadata": meta,
        "refusal": refusal
    }


@app.get("/runs/{run_id}/report", response_class=HTMLResponse)
async def view_report(run_id: str):
    """Serve the interactive Pareto frontier report HTML."""
    report_file = RUNS_DIR / run_id / "frontier_report.html"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail="Report not found for this run.")
    with open(report_file, "r") as f:
        return HTMLResponse(content=f.read())


@app.get("/runs/{run_id}/package")
async def download_package(run_id: str):
    """Download the production package ZIP bundle."""
    pkg_file = RUNS_DIR / run_id / f"{run_id}_production_package.zip"
    if not pkg_file.exists():
        raise HTTPException(status_code=404, detail="Production package ZIP not found.")
    return FileResponse(
        path=str(pkg_file),
        filename=f"{run_id}_production_package.zip",
        media_type="application/zip"
    )


@app.get("/runs/{run_id}/setup_sheet", response_class=HTMLResponse)
async def view_setup_sheet(run_id: str):
    """Serve the machinist setup sheet."""
    sheet_file = RUNS_DIR / run_id / "SETUP_SHEET.md"
    if not sheet_file.exists():
        raise HTTPException(status_code=404, detail="Setup sheet not found.")
    with open(sheet_file, "r") as f:
        content = f.read()
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
    <title>Setup Sheet - {run_id}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; max-width: 900px; margin: auto; line-height: 1.6; }}
      pre {{ background: #1e293b; padding: 1.5rem; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; font-family: monospace; }}
    </style></head><body><pre>{content}</pre></body></html>"""
    return HTMLResponse(content=html)


@app.get("/runs/{run_id}/mesh/{strategy}")
async def get_run_mesh(run_id: str, strategy: str):
    """Serve the CAMotics simulated cut STL mesh for 3D web preview."""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found.")

    file_map = {
        "cycle_time": "1_cycle_time_cut.stl",
        "accuracy_tuned": "2_accuracy_tuned_cut.stl",
        "balanced": "3_balanced_cut.stl",
        "1": "1_cycle_time_cut.stl",
        "2": "2_accuracy_tuned_cut.stl",
        "3": "3_balanced_cut.stl",
    }
    target_name = file_map.get(strategy.lower())
    if not target_name:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy}")

    stl_path = run_dir / target_name
    if not stl_path.exists():
        raise HTTPException(status_code=404, detail=f"Mesh {target_name} not found.")

    return FileResponse(path=str(stl_path), media_type="model/stl", filename=target_name)


# -----------------------------------------------------------------------------
# Embedded Web Dashboard Frontend
# -----------------------------------------------------------------------------

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CNC Machine Code Agent</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/loaders/STLLoader.js"></script>
  <style>
    :root {
      --bg-dark: #070a13;
      --bg-card: #0f172a;
      --bg-card-hover: #1e293b;
      --border: #1e293b;
      --border-accent: #334155;
      --cyan: #06b6d4;
      --cyan-glow: rgba(6, 182, 212, 0.25);
      --blue: #3b82f6;
      --emerald: #10b981;
      --amber: #f59e0b;
      --rose: #f43f5e;
      --text-main: #f8fafc;
      --text-dim: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-dark);
      color: var(--text-main);
      font-family: 'Outfit', sans-serif;
      min-height: 100vh;
      padding: 2.5rem 1.5rem;
      background-image: 
        radial-gradient(at 10% 10%, rgba(6, 182, 212, 0.08) 0px, transparent 50%),
        radial-gradient(at 90% 90%, rgba(59, 130, 246, 0.08) 0px, transparent 50%);
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
    }
    header {
      margin-bottom: 2.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .title-group h1 {
      font-size: 2.2rem;
      font-weight: 800;
      letter-spacing: -0.03em;
      background: linear-gradient(135deg, #ffffff 30%, #94a3b8 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .title-group p {
      color: var(--text-dim);
      font-size: 0.95rem;
      margin-top: 0.25rem;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.4rem 0.9rem;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      text-transform: uppercase;
      background: rgba(16, 185, 129, 0.12);
      color: var(--emerald);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .badge-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--emerald);
      box-shadow: 0 0 10px var(--emerald);
    }
    .grid-setup {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1.5rem;
      margin-bottom: 2rem;
    }
    @media (max-width: 860px) {
      .grid-setup { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.8rem;
      transition: border-color 0.2s;
    }
    .card:hover {
      border-color: var(--border-accent);
    }
    .card-title {
      font-size: 1.15rem;
      font-weight: 700;
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      gap: 0.6rem;
      color: var(--text-main);
    }
    .drop-zone {
      border: 2px dashed var(--border-accent);
      border-radius: 12px;
      padding: 2.2rem 1.5rem;
      text-align: center;
      cursor: pointer;
      transition: all 0.2s;
      background: rgba(15, 23, 42, 0.4);
    }
    .drop-zone:hover, .drop-zone.dragover {
      border-color: var(--cyan);
      background: rgba(6, 182, 212, 0.05);
      box-shadow: 0 0 20px var(--cyan-glow);
    }
    .drop-zone svg {
      width: 44px;
      height: 44px;
      stroke: var(--cyan);
      margin-bottom: 0.8rem;
    }
    .drop-zone p {
      font-size: 0.95rem;
      color: var(--text-dim);
    }
    .drop-zone span {
      color: var(--cyan);
      font-weight: 600;
    }
    select, input[type="file"] {
      width: 100%;
      padding: 0.85rem 1rem;
      background: #090d16;
      border: 1px solid var(--border-accent);
      border-radius: 8px;
      color: var(--text-main);
      font-family: inherit;
      font-size: 0.95rem;
      margin-top: 0.5rem;
      outline: none;
    }
    select:focus {
      border-color: var(--cyan);
    }
    .btn-launch {
      width: 100%;
      padding: 1.1rem;
      background: linear-gradient(135deg, #06b6d4, #2563eb);
      color: white;
      border: none;
      border-radius: 12px;
      font-size: 1.05rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.75rem;
      margin-top: 1.5rem;
      box-shadow: 0 4px 20px rgba(6, 182, 212, 0.3);
    }
    .btn-launch:hover:not(:disabled) {
      transform: translateY(-2px);
      box-shadow: 0 6px 25px rgba(6, 182, 212, 0.45);
    }
    .btn-launch:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      filter: grayscale(1);
    }
    /* Console Log Drawer */
    .console-card {
      display: none;
      background: #040711;
      border: 1px solid #1e293b;
      border-radius: 16px;
      margin-bottom: 2rem;
      overflow: hidden;
    }
    .console-header {
      background: #090e1a;
      padding: 0.85rem 1.25rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid #1e293b;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
      color: var(--text-dim);
    }
    .console-body {
      padding: 1.25rem;
      height: 380px;
      overflow-y: auto;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
      line-height: 1.5;
      color: #38bdf8;
      white-space: pre-wrap;
    }
    /* Report & Results View */
    .report-card {
      display: none;
      margin-top: 2rem;
    }
    .results-banner {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.8rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1.2rem;
      margin-bottom: 1.5rem;
    }
    .btn-action {
      display: inline-flex;
      align-items: center;
      gap: 0.6rem;
      padding: 0.75rem 1.4rem;
      border-radius: 8px;
      font-weight: 600;
      font-size: 0.95rem;
      text-decoration: none;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-zip {
      background: var(--emerald);
      color: white;
    }
    .btn-zip:hover {
      background: #059669;
      box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4);
    }
    .btn-secondary {
      background: #1e293b;
      color: var(--text-main);
      border: 1px solid var(--border-accent);
    }
    .btn-secondary:hover {
      background: #334155;
    }
    iframe {
      width: 100%;
      height: 900px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: #090d16;
    }
    .refusal-box {
      display: none;
      background: rgba(244, 63, 94, 0.1);
      border: 1px solid rgba(244, 63, 94, 0.4);
      border-radius: 12px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      color: #fda4af;
    }
    /* 3D Machined Mesh Viewport */
    .viewer-card {
      margin-bottom: 2rem;
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.5rem;
    }
    .strategy-btn-group {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }
    .pill-btn {
      background: #1e293b;
      color: var(--text-dim);
      border: 1px solid var(--border-accent);
      padding: 0.45rem 1rem;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    .pill-btn:hover {
      background: #334155;
      color: #fff;
    }
    .pill-btn.active {
      background: rgba(245, 158, 11, 0.18);
      color: #fbbf24;
      border-color: #f59e0b;
      box-shadow: 0 0 12px rgba(245, 158, 11, 0.25);
    }
  </style>
</head>
<body>

<div class="container">
  <header>
    <div class="title-group">
      <h1>CNC Machine Code Agent</h1>
      <p>Deterministic CAD Feature Extraction · Physics-Verified G-Code · Pareto Frontier Optimization</p>
    </div>
    <div class="badge">
      <div class="badge-dot"></div>
      OpenCASCADE & CAMotics Ready
    </div>
  </header>

  <!-- Setup Section -->
  <div class="grid-setup">
    <!-- Option A: Custom CAD Upload -->
    <div class="card">
      <div class="card-title">
        <svg width="22" height="22" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
        Option A: Upload Your CAD File
      </div>
      <div class="drop-zone" id="dropZone">
        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
        <p>Drag and drop your <strong>.step</strong>, <strong>.stp</strong>, or <strong>.dxf</strong> here</p>
        <p style="margin-top: 0.5rem; font-size: 0.85rem;">or <span id="browseBtn">browse your files</span></p>
        <input type="file" id="cadFileInput" accept=".step,.stp,.dxf" style="display: none;">
      </div>
      <div id="fileSelectedLabel" style="margin-top: 0.8rem; font-size: 0.9rem; color: var(--cyan); display: none;"></div>
    </div>

    <!-- Option B: Pre-loaded Benchmark Geometries -->
    <div class="card">
      <div class="card-title">
        <svg width="22" height="22" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
        Option B: Select Benchmark Part
      </div>
      <p style="font-size: 0.9rem; color: var(--text-dim); margin-bottom: 0.5rem;">
        Choose from pre-engineered industrial benchmark geometries:
      </p>
      <select id="benchmarkSelect">
        <option value="">-- Choose a CAD Benchmark Part --</option>
      </select>

      <div style="margin-top: 1.5rem;">
        <label style="font-size: 0.9rem; font-weight: 600; color: var(--text-dim);">Max Closed-Loop Iterations:</label>
        <select id="maxIterSelect" style="margin-top: 0.4rem;">
          <option value="1">1 Iteration (Fast Verification & Analysis)</option>
          <option value="2">2 Iterations (Self-Correcting Tuning)</option>
          <option value="3" selected>3 Iterations (Full Pareto Convergence)</option>
        </select>
      </div>

      <button class="btn-launch" id="btnLaunch">
        <svg width="20" height="20" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM9.555 7.168A1 1 0 008 8v4a1 1 0 001.555.832l3-2a1 1 0 000-1.664l-3-2z" clip-rule="evenodd"/></svg>
        Run Autonomous Closed-Loop Pipeline
      </button>
    </div>
  </div>

  <!-- Live Terminal Console -->
  <div class="console-card" id="consoleCard">
    <div class="console-header">
      <span id="consoleStatusLabel">⚡ PIPELINE RUNNING...</span>
      <span id="runIdBadge"></span>
    </div>
    <div class="console-body" id="consoleBody"></div>
  </div>

  <!-- Refusal Notice Box (if formal refusal triggered) -->
  <div class="refusal-box" id="refusalBox">
    <h3 style="font-size: 1.2rem; font-weight: 700; margin-bottom: 0.5rem;">🛑 FORMAL MACHINABILITY REFUSAL ISSUED (REQ.md)</h3>
    <p id="refusalReasonText"></p>
    <p id="refusalResolutionText" style="margin-top: 0.5rem; font-weight: 600;"></p>
  </div>

  <!-- Final Results & Interactive Report View -->
  <div class="report-card" id="reportCard">
    <div class="results-banner">
      <div>
        <h2 style="font-size: 1.4rem; font-weight: 700;">✅ Verification & G-Code Complete</h2>
        <p style="color: var(--text-dim); font-size: 0.95rem; margin-top: 0.25rem;">
          All Pareto strategies synthesized, collision-tested in CAMotics, and mathematically audited.
        </p>
      </div>
      <div style="display: flex; gap: 0.75rem; flex-wrap: wrap;">
        <a id="btnDownloadZip" class="btn-action btn-zip" href="#" download>
          <svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          Download Production Bundle (.zip)
        </a>
        <a id="btnSetupSheet" class="btn-action btn-secondary" href="#" target="_blank">
          Operator Setup Sheet
        </a>
        <a id="btnFullscreenReport" class="btn-action btn-secondary" href="#" target="_blank">
          Open Report in New Tab ↗
        </a>
      </div>
    </div>

    <!-- 3D Machined Workpiece Simulation (CAMotics Voxel Cut) -->
    <div class="viewer-card" id="viewerCard">
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.2rem;">
        <div style="display: flex; align-items: center; gap: 0.65rem;">
          <svg width="24" height="24" fill="none" stroke="#fbbf24" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10l-2 1m0 0l-2-1m2 1v2.5M20 7l-2 1m2-1l-2-1m2 1v2.5M14 4l-2-1-2 1M4 7l2-1M4 7l2 1M4 7v2.5M12 21l-2-1m2 1l2-1m-2 1v-2.5M6 18l-2-1v-2.5M18 18l2-1v-2.5"/></svg>
          <div>
            <h3 style="font-size: 1.25rem; font-weight: 700; color: #fff;">3D Virtual Machining Inspection (CAMotics Voxel Cut)</h3>
            <p style="font-size: 0.85rem; color: var(--text-dim); margin-top: 0.15rem;">Interactive subtractive 3D solid mesh carved by virtual CNC cutting tools</p>
          </div>
        </div>
        <div class="strategy-btn-group">
          <button class="pill-btn active" id="btnStlAcc" onclick="switchStrategy('accuracy_tuned')">🟡 2. Accuracy Tuned Cut</button>
          <button class="pill-btn" id="btnStlBal" onclick="switchStrategy('balanced')">🟡 3. Balanced Cut</button>
          <button class="pill-btn" id="btnStlCt" onclick="switchStrategy('cycle_time')">🟡 1. Cycle Time Cut</button>
          <button class="pill-btn" style="color: var(--cyan); border-color: var(--cyan);" onclick="reset3DView()">⟲ Reset Camera</button>
        </div>
      </div>
      <div style="position: relative; width: 100%; height: 500px; background: #070a13; border-radius: 12px; border: 1px solid var(--border); overflow: hidden;">
        <div id="threeContainer" style="width: 100%; height: 100%;"></div>
        <div id="viewerLoadingOverlay" style="position: absolute; inset: 0; display: none; align-items: center; justify-content: center; background: rgba(7,10,19,0.8); color: var(--cyan); font-family: 'JetBrains Mono'; font-size: 0.95rem;">
          <span>Loading 3D Machined Mesh...</span>
        </div>
        <div style="position: absolute; bottom: 12px; left: 16px; font-size: 0.8rem; color: var(--text-dim); background: rgba(15,23,42,0.85); padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border); pointer-events: none;">
          🖱️ Left Click: Rotate • Right Click: Pan • Scroll: Zoom
        </div>
        <div id="strategyBadge" style="position: absolute; top: 12px; left: 16px; font-size: 0.85rem; font-family: 'JetBrains Mono'; font-weight: 700; color: #fbbf24; background: rgba(15,23,42,0.9); padding: 5px 12px; border-radius: 6px; border: 1px solid rgba(251,191,36,0.3); pointer-events: none;">
          Strategy: ACCURACY_TUNED
        </div>
      </div>
    </div>

    <!-- Embedded Pareto Frontier Report -->
    <iframe id="reportFrame" src="about:blank"></iframe>
  </div>

</div>

<script>
  let selectedFile = null;
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('cadFileInput');
  const browseBtn = document.getElementById('browseBtn');
  const fileSelectedLabel = document.getElementById('fileSelectedLabel');
  const benchmarkSelect = document.getElementById('benchmarkSelect');
  const maxIterSelect = document.getElementById('maxIterSelect');
  const btnLaunch = document.getElementById('btnLaunch');
  const consoleCard = document.getElementById('consoleCard');
  const consoleBody = document.getElementById('consoleBody');
  const consoleStatus = document.getElementById('consoleStatusLabel');
  const runIdBadge = document.getElementById('runIdBadge');
  const reportCard = document.getElementById('reportCard');
  const reportFrame = document.getElementById('reportFrame');
  const refusalBox = document.getElementById('refusalBox');
  const btnDownloadZip = document.getElementById('btnDownloadZip');
  const btnSetupSheet = document.getElementById('btnSetupSheet');
  const btnFullscreenReport = document.getElementById('btnFullscreenReport');

  // Load benchmark list on start
  fetch('/api/benchmarks')
    .then(r => r.json())
    .then(data => {
      data.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.filename;
        opt.textContent = `${p.filename} — ${p.name}`;
        benchmarkSelect.appendChild(opt);
      });
    });

  // Drag and drop handlers
  browseBtn.addEventListener('click', () => fileInput.click());
  dropZone.addEventListener('click', () => fileInput.click());

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
      handleFileSelected(fileInput.files[0]);
    }
  });

  function handleFileSelected(file) {
    selectedFile = file;
    fileSelectedLabel.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
    fileSelectedLabel.style.display = 'block';
    benchmarkSelect.value = ''; // Reset benchmark dropdown
  }

  benchmarkSelect.addEventListener('change', () => {
    if (benchmarkSelect.value) {
      selectedFile = null;
      fileInput.value = '';
      fileSelectedLabel.style.display = 'none';
    }
  });

  // Launch pipeline
  btnLaunch.addEventListener('click', async () => {
    if (!selectedFile && !benchmarkSelect.value) {
      alert('Please upload a CAD file (.step, .dxf) or choose a benchmark part!');
      return;
    }

    btnLaunch.disabled = true;
    reportCard.style.display = 'none';
    refusalBox.style.display = 'none';
    consoleCard.style.display = 'block';
    consoleBody.textContent = 'Connecting to Autonomous Agent Core...\\n';
    consoleStatus.textContent = '⚡ PIPELINE RUNNING...';

    const formData = new FormData();
    if (selectedFile) {
      formData.append('cad_file', selectedFile);
    } else {
      formData.append('benchmark_name', benchmarkSelect.value);
    }
    formData.append('max_iterations', maxIterSelect.value);

    try {
      const res = await fetch('/api/run', { method: 'POST', body: formData });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Launch failed');

      const runId = data.run_id;
      runIdBadge.textContent = `RUN: ${runId}`;

      // Connect to SSE stream
      const evtSource = new EventSource(`/api/stream/${runId}`);

      evtSource.onmessage = (e) => {
        if (e.data.startsWith('__STATUS_FINISHED__:')) {
          const finalStatus = e.data.split(':')[1];
          evtSource.close();
          btnLaunch.disabled = false;
          handleRunCompleted(runId, finalStatus);
        } else {
          consoleBody.textContent += e.data;
          consoleBody.scrollTop = consoleBody.scrollHeight;
        }
      };

      evtSource.onerror = () => {
        evtSource.close();
        btnLaunch.disabled = false;
        consoleStatus.textContent = '⚠️ STREAM DISCONNECTED';
      };

    } catch (err) {
      alert(`Error starting run: ${err.message}`);
      btnLaunch.disabled = false;
    }
  });

  async function handleRunCompleted(runId, finalStatus) {
    if (finalStatus === 'refused') {
      consoleStatus.textContent = '🛑 MACHINABILITY REFUSAL ISSUED';
      const statusRes = await fetch(`/api/status/${runId}`);
      const statusData = await statusRes.json();
      if (statusData.refusal) {
        document.getElementById('refusalReasonText').textContent = statusData.refusal.reason;
        document.getElementById('refusalResolutionText').textContent = statusData.refusal.resolution_instructions;
        refusalBox.style.display = 'block';
      }
      return;
    }

    if (finalStatus === 'completed') {
      consoleStatus.textContent = '✅ PIPELINE COMPLETED';
      btnDownloadZip.href = `/runs/${runId}/package`;
      btnSetupSheet.href = `/runs/${runId}/setup_sheet`;
      btnFullscreenReport.href = `/runs/${runId}/report`;
      reportFrame.src = `/runs/${runId}/report`;
      reportCard.style.display = 'block';

      currentRunId = runId;
      setTimeout(() => {
        loadMesh(runId, 'accuracy_tuned');
      }, 150);
    } else {
      consoleStatus.textContent = '❌ EXECUTION ENCOUNTERED ERROR';
    }
  }

  // ---------------------------------------------------------------------------
  // Three.js 3D Machined Mesh Viewport Controller (CQ-Editor Gold Theme)
  // ---------------------------------------------------------------------------
  let currentRunId = null;
  let activeStrategy = 'accuracy_tuned';
  let scene, camera, renderer, controls, currentMesh, currentEdges;
  let isViewerInitialized = false;

  function initThreeViewer() {
    const container = document.getElementById('threeContainer');
    if (!container || isViewerInitialized) return;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070a13);

    const width = container.clientWidth || 800;
    const height = container.clientHeight || 500;

    camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
    camera.position.set(150, -180, 160);
    camera.up.set(0, 0, 1); // Z-up for standard CNC spindle orientation

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(width, height);
    renderer.shadowMap.enabled = true;
    container.appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;

    // Lighting setup for depth, shadows, and metallic reflections
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.85);
    dirLight1.position.set(160, -200, 240);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0x60a5fa, 0.35);
    dirLight2.position.set(-160, 200, 160);
    scene.add(dirLight2);

    const bottomBounce = new THREE.DirectionalLight(0xfef08a, 0.25);
    bottomBounce.position.set(0, 0, -150);
    scene.add(bottomBounce);

    // Subtle XY Grid Helper
    const grid = new THREE.GridHelper(300, 30, 0x1e293b, 0x0f172a);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -15;
    scene.add(grid);

    window.addEventListener('resize', () => {
      if (!container || !renderer) return;
      const w = container.clientWidth;
      const h = container.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });

    function animate() {
      requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    }
    animate();
    isViewerInitialized = true;
  }

  function loadMesh(runId, strategy) {
    initThreeViewer();
    const loadingOverlay = document.getElementById('viewerLoadingOverlay');
    const badge = document.getElementById('strategyBadge');
    loadingOverlay.style.display = 'flex';
    badge.textContent = `Loading: ${strategy.toUpperCase()}...`;

    const loader = new THREE.STLLoader();
    const url = `/runs/${runId}/mesh/${strategy}`;

    loader.load(url, function (geometry) {
      if (currentMesh) scene.remove(currentMesh);
      if (currentEdges) scene.remove(currentEdges);

      geometry.computeVertexNormals();
      geometry.center();

      // CadQuery yellow material (warm golden yellow with smooth metallic sheen)
      const material = new THREE.MeshStandardMaterial({
        color: 0xfbbf24,
        roughness: 0.32,
        metalness: 0.22,
        side: THREE.DoubleSide
      });

      currentMesh = new THREE.Mesh(geometry, material);
      scene.add(currentMesh);

      // Subtle edge lines to highlight pockets, bosses, and drilled hole rims
      const edgesGeom = new THREE.EdgesGeometry(geometry, 28);
      currentEdges = new THREE.LineSegments(
        edgesGeom,
        new THREE.LineBasicMaterial({ color: 0xd97706, transparent: true, opacity: 0.4 })
      );
      scene.add(currentEdges);

      // Frame camera to fit object bounding sphere
      geometry.computeBoundingSphere();
      const radius = geometry.boundingSphere.radius;
      camera.position.set(radius * 1.5, -radius * 1.8, radius * 1.6);
      controls.target.set(0, 0, 0);
      controls.update();

      loadingOverlay.style.display = 'none';
      badge.textContent = `Strategy: ${strategy.toUpperCase()} Cut Mesh`;
    }, undefined, function (error) {
      loadingOverlay.style.display = 'none';
      badge.textContent = `Mesh unavailable for ${strategy}`;
    });
  }

  function switchStrategy(strategy) {
    if (!currentRunId) return;
    activeStrategy = strategy;
    document.querySelectorAll('.strategy-btn-group .pill-btn').forEach(b => b.classList.remove('active'));
    if (strategy === 'accuracy_tuned') document.getElementById('btnStlAcc').classList.add('active');
    if (strategy === 'balanced') document.getElementById('btnStlBal').classList.add('active');
    if (strategy === 'cycle_time') document.getElementById('btnStlCt').classList.add('active');
    loadMesh(currentRunId, strategy);
  }

  function reset3DView() {
    if (currentMesh && currentMesh.geometry && currentMesh.geometry.boundingSphere) {
      const r = currentMesh.geometry.boundingSphere.radius;
      camera.position.set(r * 1.5, -r * 1.8, r * 1.6);
      controls.target.set(0, 0, 0);
      controls.update();
    }
  }
</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the interactive web app dashboard."""
    return HTMLResponse(content=HTML_DASHBOARD)


def main():
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="CNC Machine Code Agent Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    args = parser.parse_args()

    print("=" * 75)
    print(" 🚀 CNC MACHINE CODE AGENT - WEB SERVICE RUNNING")
    print("=" * 75)
    print(f" Local Web UI    : \033[1;36mhttp://localhost:{args.port}\033[0m")
    print(f" Network Web UI  : \033[1;36mhttp://{args.host}:{args.port}\033[0m")
    print(f" API Docs        : \033[1;32mhttp://localhost:{args.port}/docs\033[0m")
    print("=" * 75 + "\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
