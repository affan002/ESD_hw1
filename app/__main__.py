"""Entry point: `python -m app` runs the API with a single uvicorn worker."""

import uvicorn

from app.config import load_settings
from app.logging_setup import setup_logging
from app.main import create_app


def main() -> None:
    settings = load_settings()
    setup_logging("sensor-api", settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
