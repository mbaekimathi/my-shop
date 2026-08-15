"""Gunicorn configuration for production WSGI deployment.

Capacity rule of thumb:
  GUNICORN_WORKERS × GUNICORN_THREADS + ~20 reserve  ≤  MySQL max_connections
Validate with: python scripts/test_page_hop_loop.py  (gunicorn-mysql capacity check)
"""

import multiprocessing
import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")
# Cap the auto worker count so a high-CPU host does not exhaust MySQL
# max_connections under persistent CONN_MAX_AGE. Override with GUNICORN_WORKERS.
_auto_workers = min(multiprocessing.cpu_count() * 2 + 1, 4)
workers = int(os.getenv("GUNICORN_WORKERS", str(_auto_workers)))
# gthread overlaps I/O (print scan/relay) so one slow LAN call does not stall the worker.
threads = int(os.getenv("GUNICORN_THREADS", "2"))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread" if threads > 1 else "sync")
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
keepalive = 5
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "50"))
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
