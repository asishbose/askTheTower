"""`demo-ui` / `python -m demo_ui`: one process, one feed poller, one runner (doc 11 §2). Logs are JSON lines with
event names and counts; uvicorn's access log is off (§8.3)."""

from __future__ import annotations

import json
import logging
import sys

from demo_ui.redact import mask


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {"level": record.levelname, "logger": record.name, "msg": mask(record.getMessage())},
            ensure_ascii=False,
        )


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def main() -> None:
    import uvicorn

    from demo_ui.app import create_app
    from demo_ui.config import ConfigError, Settings
    from demo_ui.deps import build_deps

    configure_logging()
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        logging.getLogger("demo_ui").error("config: %s", e)
        sys.exit(2)
    app = create_app(build_deps(settings))
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
