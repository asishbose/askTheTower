# syntax=docker/dockerfile:1.7
# Build from the repository root:  docker build -f services/mock-carrier/Dockerfile -t ask-the-tower/mock-carrier .
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
# Dependencies install with the image's default CA roots. Only if that fails and `make push` was given an
# EXTRA_CA_FILE (optional, root .env) is the install retried trusting that bundle too; the file is mounted for this
# step only and never written to a layer.
RUN --mount=type=secret,id=extra_ca,required=false \
    deps_install() { \
        rm -rf /venv && \
        uv export --frozen --no-dev --no-emit-workspace --no-hashes --package mock-carrier -o requirements.txt && \
        uv venv /venv && \
        uv pip install --python /venv/bin/python --no-cache -r requirements.txt && \
        uv pip install --python /venv/bin/python --no-cache --no-deps ./services/mock-carrier; \
    }; \
    deps_install || { \
      [ -s /run/secrets/extra_ca ] && echo 'retrying with EXTRA_CA_FILE' && \
      ( export SSL_CERT_FILE=/run/secrets/extra_ca REQUESTS_CA_BUNDLE=/run/secrets/extra_ca UV_NATIVE_TLS=1; deps_install ); \
    }

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=build /venv /venv
WORKDIR /app
COPY specs/camara ./specs/camara
COPY scenarios ./scenarios
COPY services/mock-carrier/config ./config
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOCK_PORT=8443 \
    MOCK_SPECS_DIR=/app/specs/camara \
    MOCK_SCENARIOS_DIR=/app/scenarios \
    MOCK_CLIENTS_FILE=/app/config/clients.yaml
USER 10001
EXPOSE 8443
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s CMD ["python", "-c", "import os,ssl,urllib.request as u; s='https' if os.environ.get('MOCK_TLS_CERT') else 'http'; u.urlopen(f\"{s}://127.0.0.1:{os.environ.get('MOCK_PORT','8443')}/healthz\", timeout=2, context=ssl._create_unverified_context())"]
CMD ["python", "-m", "mock_carrier"]
