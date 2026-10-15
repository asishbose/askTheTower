# syntax=docker/dockerfile:1.7
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.10.11 /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services/ref-client ./services/ref-client
# Only the client's own dependencies: no Tower, no policy, no consent, no carrier client in this image. Installed
# with uv like the other images.
# Dependencies install with the image's default CA roots. Only if that fails and `make push` was given an
# EXTRA_CA_FILE (optional, root .env) is the install retried trusting that bundle too; the file is mounted for this
# step only and never written to a layer.
RUN --mount=type=secret,id=extra_ca,required=false \
    deps_install() { \
        rm -rf /venv && \
        uv export --frozen --no-dev --no-emit-workspace --package ref-client -o requirements.txt && \
        uv venv /venv && \
        uv pip install --python /venv/bin/python --no-cache -r requirements.txt && \
        uv pip install --python /venv/bin/python --no-cache --no-deps ./services/ref-client; \
    }; \
    deps_install || { \
      [ -s /run/secrets/extra_ca ] && echo 'retrying with EXTRA_CA_FILE' && \
      ( export SSL_CERT_FILE=/run/secrets/extra_ca REQUESTS_CA_BUNDLE=/run/secrets/extra_ca UV_NATIVE_TLS=1; deps_install ); \
    }

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
RUN useradd -r -u 10001 -m app && mkdir /out && chown app /out
COPY --from=build /venv /venv
# The web chat page (09 §6.4), served at / only when WEB_CHAT_DIR points here (compose sets it; AWS uses S3).
COPY services/web-chat/index.html services/web-chat/app.js services/web-chat/styles.css /app/web-chat/
ENV PATH=/venv/bin:$PATH PYTHONUNBUFFERED=1 REF_ARTIFACTS_DIR=/out REF_CLIENT_HTTP_PORT=8080
USER app
WORKDIR /home/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import ref_client" || exit 1
# Default: the HTTP app (POST /invocations, /ping on 8080 — what AgentCore Runtime runs). The CLI is the same
# entrypoint with another command: `docker run … ref-client demo`, `… say "…"`, `… corpus`.
ENTRYPOINT ["ref-client"]
CMD ["serve"]
