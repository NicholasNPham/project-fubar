"""Logging setup, ported from DALYN.

One dated file per day in logs/, plus console output. Every module gets its
logger with get_logger(__name__) so each line records which file it came from.
"""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

ROOT_LOGGER_NAME = "fubar"
LOG_FILE_NAME = "fubar.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_DAYS_KEPT = 90


def setup_logging(log_dir: Path, console_level: int = logging.INFO) -> None:
    """Configure the root logger. Call once, at startup.

    Args:
        log_dir: Directory for log files. Created if it does not exist.
        console_level: Minimum level printed to the console. The file always
            records DEBUG and above.
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger(ROOT_LOGGER_NAME)
    root_logger.setLevel(logging.DEBUG)

    if root_logger.handlers:
        return

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = TimedRotatingFileHandler(
        filename=log_dir / LOG_FILE_NAME,
        when="midnight",
        interval=1,
        backupCount=LOG_DAYS_KEPT,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


def get_logger(module_name: str) -> logging.Logger:
    """Return the logger for a module.

    Args:
        module_name: Pass __name__.

    Returns:
        A child of the root logger, e.g. "fubar.stac".
    """
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{module_name}")
