"""Logging setup."""

import logging
import sys


def configure_logging(level: str) -> None:
    """Configure structured-ish logging to stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
