import logging
import sys

from pythonjsonlogger import jsonlogger


def setup_logging() -> None:
    """Structured JSON logs to stdout so a collector can parse them."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # uvicorn access logs are redundant with our middleware access line
    logging.getLogger("uvicorn.access").disabled = True
