"""Values shared by the pipeline, the local supervisor and the AWS handlers (kept import-light on purpose)."""

from __future__ import annotations

from datetime import timedelta

# Exit code the worker uses for "job returned to the queue; run me again" (EX_TEMPFAIL).
RETRY_EXIT_CODE = 75
HEARTBEAT_SECONDS = 30.0
# A job whose heartbeat is fresher than this is owned by a live worker: a second claim is refused.
ALIVE_WITHIN = timedelta(minutes=2)
