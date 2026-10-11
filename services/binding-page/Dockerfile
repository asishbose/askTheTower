# syntax=docker/dockerfile:1.7
# Build from the repository root:  docker build -f services/binding-page/Dockerfile -t ask-the-tower/binding-page .
# The same image serves the container (uvicorn, `python -m binding_page`) and, on AWS, a Lambda container image
# whose handler is `binding_page.app.handler` (Mangum) — set by the Lambda function's image config, not here.
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
RUN uv export --frozen --no-dev --no-emit-workspace --no-hashes --package binding-page -o requirements.txt \
 && uv venv /venv \
 && uv pip install --python /venv/bin/python --no-cache -r requirements.txt \
 && uv pip install --python /venv/bin/python --no-cache --no-deps \
      ./packages/tower-policy ./packages/tower-consent ./packages/tower-audit ./packages/camara-client \
      ./services/binding-page

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /venv /venv
WORKDIR /app
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TOWER_ENV=local \
    PORT=8081
USER 10001
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s CMD ["python", "-c", "import os,urllib.request as u; u.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8081')}/healthz\", timeout=2)"]
CMD ["python", "-m", "binding_page"]
