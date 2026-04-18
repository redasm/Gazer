# ============================================================
# Gazer — Multi-stage Docker build
# ============================================================
# Usage:
#   docker build -t gazer .                       # core only
#   docker build -t gazer --build-arg EXTRAS=perception,browser .
# ============================================================

# --- Stage 1: Python dependencies ---
FROM python:3.11-slim AS python-deps

WORKDIR /build

# Improve pip resilience in unstable networks during image build.
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=20 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libs needed by some pip packages (numpy, Pillow, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

# Install core dependencies
RUN pip install --no-cache-dir --retries ${PIP_RETRIES} --timeout ${PIP_DEFAULT_TIMEOUT} .

# Perception imports are loaded by default at runtime.
RUN pip install --no-cache-dir --retries ${PIP_RETRIES} --timeout ${PIP_DEFAULT_TIMEOUT} "sounddevice>=0.5.0"

# Optional extras via build arg (comma-separated, e.g. "perception,browser")
ARG EXTRAS=""
RUN if [ -n "$EXTRAS" ]; then pip install --no-cache-dir --retries ${PIP_RETRIES} --timeout ${PIP_DEFAULT_TIMEOUT} ".[$EXTRAS]"; fi

# --- Stage 2: Build web frontend ---
FROM node:20-slim AS web-build

WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --ignore-scripts 2>/dev/null || npm install --ignore-scripts
COPY web/ ./
RUN npm run build

# --- Stage 3: Final image ---
FROM python:3.11-slim AS runtime

# System runtime libs (Pillow, serial, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libportaudio2 && \
    rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r gazer && useradd -r -g gazer -m gazer

WORKDIR /app

# Copy installed Python packages from deps stage
COPY --from=python-deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy source code
COPY main.py ./
COPY src/ ./src/
COPY perception/ ./perception/
COPY hardware/ ./hardware/

# Copy built web frontend
COPY --from=web-build /web/dist ./web/dist

# Copy default assets & config templates
COPY assets/ ./assets/
COPY config/ ./config/
COPY skills/ ./skills/
COPY .env.example ./.env.example

# Create data directories with correct ownership
RUN mkdir -p data logs && chown -R gazer:gazer /app

# Ports
EXPOSE 8080

# Runtime defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ADMIN_API_HOST=0.0.0.0 \
    GAZER_PROJECT_ROOT=/app

USER gazer

ENTRYPOINT ["python", "main.py"]
