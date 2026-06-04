"""Logging setup for Agent Session Workboard."""

import logging
import sys
from pathlib import Path


def setup_logging(
    level: int = logging.INFO,
    log_file: Path | None = None,
) -> logging.Logger:
    """Configure root logger with console and optional file output.

    Args:
        level: Logging level (default INFO).
        log_file: Optional path to a log file.

    Returns:
        The root logger.
    """
    logger = logging.getLogger("agentboard")
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # File handler (optional)
    if log_file:
        log_file = Path(log_file).expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s:%(lineno)d | %(message)s"
        )
        file_handler.setFormatter(file_fmt)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "agentboard") -> logging.Logger:
    """Get a logger for the given module name."""
    return logging.getLogger(f"agentboard.{name}")
