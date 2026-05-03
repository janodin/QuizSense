"""
Timing and diagnostic logger for QuizSense upload processing.

Logs every step with timestamps to a dedicated file:
  quizsense_processing.log

Usage:
  Just upload a file normally — the logger auto-activates during processing.
  Then read quizsense_processing.log to see where time is spent.

Log format:
  [2026-05-04 12:00:00] [SESSION 24] STEP_NAME | duration=X.Xs | details
"""

import logging
import time
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent.parent / "quizsense_processing.log"

_file_handler = None


def _get_logger():
    """Get or create the processing logger with file handler."""
    global _file_handler
    logger = logging.getLogger("quizsense.timing")

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        _file_handler = logging.FileHandler(str(LOG_FILE), mode="a", encoding="utf-8")
        _file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "[%(asctime)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _file_handler.setFormatter(formatter)
        logger.addHandler(_file_handler)
        logger.propagate = False

    return logger


class ProcessingTimer:
    """
    Context manager that logs start/end/duration for a processing step.

    Usage:
        with ProcessingTimer(session_id, "TEXT_EXTRACTION") as timer:
            # ... do work ...
            timer.detail("Extracted 5000 chars from file.pdf")

    Automatically logs:
        [SESSION 24] START  TEXT_EXTRACTION
        [SESSION 24] DETAIL TEXT_EXTRACTION | Extracted 5000 chars from file.pdf
        [SESSION 24] END    TEXT_EXTRACTION | duration=1.23s
    """

    def __init__(self, session_id, step_name):
        self.session_id = session_id
        self.step_name = step_name
        self.start_time = None
        self.logger = _get_logger()
        self.details = []

    def __enter__(self):
        self.start_time = time.time()
        self.logger.info("[SESSION %s] START  %s", self.session_id, self.step_name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.time() - self.start_time
        if exc_type:
            self.logger.error(
                "[SESSION %s] ERROR  %s | duration=%.2fs | %s: %s",
                self.session_id,
                self.step_name,
                elapsed,
                exc_type.__name__,
                str(exc_val)[:500],
            )
        else:
            self.logger.info(
                "[SESSION %s] END    %s | duration=%.2fs",
                self.session_id,
                self.step_name,
                elapsed,
            )
        return False  # Don't suppress exceptions

    def detail(self, message):
        """Log a detail message within this step."""
        elapsed = time.time() - self.start_time
        self.logger.info(
            "[SESSION %s] DETAIL %s | at %.2fs | %s",
            self.session_id,
            self.step_name,
            elapsed,
            message,
        )


def log_summary(session_id, results):
    """Log a summary of all timings at the end of processing."""
    logger = _get_logger()
    logger.info("=" * 70)
    logger.info("[SESSION %s] PROCESSING COMPLETE", session_id)
    for step, duration in results.items():
        logger.info("[SESSION %s]   %-30s %.2fs", session_id, step, duration)
    logger.info("=" * 70)