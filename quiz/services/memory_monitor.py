"""
Memory monitoring utility for QuizSense.

Logs RSS memory usage at key pipeline steps to help diagnose leaks
and verify optimizations.
"""

import logging
import threading

logger = logging.getLogger(__name__)

# Lazy import so psutil is optional
_psutil = None
_lock = threading.Lock()


def _get_psutil():
    global _psutil
    if _psutil is None:
        try:
            import psutil
            _psutil = psutil
        except ImportError:
            _psutil = False
    return _psutil


def log_memory(label: str = ""):
    """Log current process RSS in MB. No-op if psutil is missing."""
    psutil = _get_psutil()
    if not psutil:
        return
    try:
        process = psutil.Process()
        mem_mb = process.memory_info().rss / 1024 / 1024
        logger.info("[MEMORY] %s: %.1f MB RSS", label, mem_mb)
    except Exception as exc:
        logger.debug("[MEMORY] Could not read memory: %s", exc)
