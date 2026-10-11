"""`python -m mock_carrier` / `mock-carrier`: serve the mock with uvicorn, configured from the env."""

from __future__ import annotations

import uvicorn

from mock_carrier.app import create_app
from mock_carrier.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 — container entrypoint; compose/ALB decide exposure
        port=settings.port,
        ssl_certfile=settings.tls_cert,
        ssl_keyfile=settings.tls_key,
        access_log=False,  # request lines can carry nothing sensitive, but keep logs to our own lines
        log_level="info",
    )


if __name__ == "__main__":
    main()
