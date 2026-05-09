from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable


class CallbackLogHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self.callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:
            pass


def configure_logging(log_path: Path, callback: Callable[[str], None] | None = None) -> logging.Logger:
    logger = logging.getLogger("netops_suite")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    if callback:
        callback_handler = CallbackLogHandler(callback)
        callback_handler.setFormatter(formatter)
        logger.addHandler(callback_handler)

    file_logging_ready = False
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        file_logging_ready = True
    except OSError as exc:
        logger.warning("File logging unavailable at %s: %s", log_path, exc)

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    if file_logging_ready:
        logger.info("Logging initialized: %s", log_path)
    else:
        logger.info("Logging initialized without file output.")
    return logger
