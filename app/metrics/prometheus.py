"""
Custom Prometheus metrics definitions for APC Speech.
Covers HTTP requests, latency, background jobs, voice search duration, and queue depth.
"""

from prometheus_client import Counter, Gauge, Histogram

# HTTP metrics
HTTP_REQUESTS_TOTAL = Counter(
    "apc_http_requests_total",
    "Total number of HTTP requests processed",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "apc_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

# Jobs metrics
JOBS_TOTAL = Counter(
    "apc_jobs_total",
    "Total audio analysis jobs processed",
    ["status", "namespace"],
)

# Voice search metrics
VOICE_SEARCH_DURATION_SECONDS = Histogram(
    "apc_voice_search_duration_seconds",
    "Voice search transcription duration in seconds",
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0],
)

# Queue depth
QUEUE_SIZE = Gauge(
    "apc_queue_size",
    "Current message depth in Redis stream queues",
    ["queue_name"],
)
