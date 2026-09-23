# Production Dockerfile for Autonomous CNC Machining Agent
# Self-contained runtime with FreeCAD, CAMotics, OpenCASCADE, and Python

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install system dependencies: FreeCAD, CAMotics, OpenGL/EGL, Python
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    freecad \
    camotics \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for blazing-fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /workspace/cnc_mc_agent

# Copy configuration and dependencies
COPY pyproject.toml README.md ./
COPY tool_library.json sample_part.step sample_part.dxf ./
COPY core/ ./core/
COPY tests/ ./tests/
COPY input_files/ ./input_files/
COPY run_pipeline.py benchmark.py ./

# Install agent as editable CLI
RUN uv pip install --system -e .

# Default entrypoint
ENTRYPOINT ["cnc-agent"]
CMD ["--help"]
