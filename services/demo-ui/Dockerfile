# syntax=docker/dockerfile:1.7
# Build from the repository root:  docker build -f services/demo-ui/Dockerfile -t ask-the-tower/demo-ui .
# Laptop-only demo tooling (docs/architecture/components/11-demo-ui.md): never pushed to ECR, never on AWS.
# The image carries the `make seed` module so Reset runs the same code (doc 11 decision 5).
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv export --frozen --no-dev --no-emit-workspace --no-hashes --package demo-ui -o requirements.txt \
 && uv venv /venv \
 && uv pip install --python /venv/bin/python --no-cache -r requirements.txt \
 && uv pip install --python /venv/bin/python --no-cache --no-deps \
      ./packages/tower-policy ./packages/tower-consent ./packages/tower-audit \
      ./services/ref-client ./services/demo-ui

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /venv /venv
COPY deploy/compose/seed/seed.py /app/seed/seed.py
WORKDIR /app
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    DEMO_UI_SEED_PATH=/app/seed/seed.py \
    DEMO_UI_PORT=8090
USER 10001
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s CMD ["python", "-c", "import os,urllib.request as u; u.urlopen(f\"http://127.0.0.1:{os.environ.get('DEMO_UI_PORT','8090')}/healthz\", timeout=2)"]
CMD ["python", "-m", "demo_ui"]
