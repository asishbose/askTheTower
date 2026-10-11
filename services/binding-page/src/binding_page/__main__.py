"""`python -m binding_page` / `binding-page`: serve the page with uvicorn (container entry point)."""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from binding_page.app import create_app_from_env

    uvicorn.run(
        create_app_from_env(),
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - container port
        port=int(os.environ.get("PORT", "8081")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        access_log=False,  # access logs would carry bind tokens in paths
    )


if __name__ == "__main__":
    main()
