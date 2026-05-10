"""
Gunicorn configuration for QuizSense.

Memory-optimized for Hetzner CX22 (4GB RAM):
- Uses 1 worker with 4 threads instead of multiple processes.
- Threads avoid duplicating Django memory across multiple worker processes.
- Workers restart periodically to prevent memory leaks.
"""

# Use threads instead of multiple processes for lower memory usage.
# On a 2-vCPU box, 1 worker × 4 threads is plenty.
workers = 1
threads = 4
worker_class = "gthread"

# Restart workers after N requests to prevent gradual memory leaks
# from numpy or other C extensions.
max_requests = 100
max_requests_jitter = 20

# Hard timeout for long-running requests (e.g. large file uploads)
timeout = 180
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Bind address (override with -b flag if needed)
bind = "0.0.0.0:8001"
