# Production Dockerfile for CNC Machine Code Agent
# Self-contained runtime packaging FreeCAD B-Rep kernel, CAMotics simulation engine, and FastAPI Web Portal

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 1. Install base utilities & configure PPAs
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    ca-certificates \
    curl \
    wget \
    git \
    gnupg \
    gpg-agent \
    dirmngr \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && add-apt-repository -y ppa:freecad-maintainers/freecad-stable \
    && apt-get update

# 2. Install Python 3.11, FreeCAD CLI, and CAMotics system dependencies
RUN apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    freecad \
    freecad-python3 \
    python3-numpy \
    libglu1-mesa \
    libqt5core5a \
    libqt5gui5 \
    libqt5opengl5 \
    libqt5widgets5 \
    libqt5websockets5 \
    && rm -rf /var/lib/apt/lists/*

# 3. Install CAMotics 1.2.0 and its native runtime dependencies (libssl1.1 & libv8)
RUN wget -q http://archive.ubuntu.com/ubuntu/pool/main/o/openssl/libssl1.1_1.1.1f-1ubuntu2.24_amd64.deb -O /tmp/libssl1.1.deb \
    && dpkg -i /tmp/libssl1.1.deb \
    && wget -q http://archive.ubuntu.com/ubuntu/pool/universe/libv/libv8-3.14/libv8-3.14.5_3.14.5.8-5ubuntu2_amd64.deb -O /tmp/libv8.deb \
    && dpkg -i /tmp/libv8.deb \
    && wget -q https://camotics.org/builds/release/debian-stable-64bit/camotics_1.2.0_amd64.deb -O /tmp/camotics_1.2.0_amd64.deb \
    && dpkg -i /tmp/camotics_1.2.0_amd64.deb \
    && rm -f /tmp/libssl1.1.deb /tmp/libv8.deb /tmp/camotics_1.2.0_amd64.deb

# 4. Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 5. Set default python3 to python3.11
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

WORKDIR /workspace/cnc_mc_agent

# 6. Copy configuration and source files
COPY pyproject.toml README.md tool_library.json ./
COPY sample_part.step sample_part.dxf ./
COPY core/ ./core/
COPY tests/ ./tests/
COPY input_files/ ./input_files/
COPY run_pipeline.py benchmark.py web_app.py ./

# 7. Install Python dependencies and agent package
RUN uv pip install --system --python /usr/bin/python3.11 -e .

# 8. Web dashboard port
EXPOSE 8000

# 9. Default container start: run the web service
CMD ["python3", "web_app.py", "--host", "0.0.0.0", "--port", "8000"]
