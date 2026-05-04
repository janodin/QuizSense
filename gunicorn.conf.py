"""
Gunicorn configuration for QuizSense.

Memory-optimized for Hetzner CX22 (4GB RAM):
- Uses 1 worker with 4 threads instead of multiple processes.
- This allows the sentence-transformers model (~400MB) to be shared
  across threads rather than duplicated per process.
- Workers restart periodically to prevent memory leaks.
"""

import multiprocessing

# Use threads instead of processes for memory sharing.
# On a 2-vCPU box, 1 worker × 4 threads is plenty.
workers = 1
threads = 4
worker_class = "gthread"

# Restart workers after N requests to prevent gradual memory leaks
# from PyTorch, numpy, or other C extensions.
max_requests = 100
max_requests_jitter = 20

# Hard timeout for long-running requests (e.g. large file uploads)
timeout = 120
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Bind address (override with -b flag if needed)
bind = "0.0.0.0:8001"
