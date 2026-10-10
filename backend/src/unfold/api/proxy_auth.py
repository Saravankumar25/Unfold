"""Client identity behind the Vercel proxy (ADR 0016).

A Function URL sees only Vercel's egress addresses, so peer-address trust cannot work. The proxy instead signs
the address it observed: `X-Unfold-Client: v1.<unix_ts>.<ip>.<hex HMAC-SHA256(secret, "<ts>|<ip>")>`. The API
accepts it only when the MAC verifies and the timestamp is fresh; anything else (a caller that bypassed the
proxy, a replay, a forged header) falls into one shared `unverified` bucket and is never given its own.
"""

from __future__ import annotations

import hashlib
import hmac
import time

HEADER = "x-unfold-client"
MAX_SKEW_SECONDS = 60
UNVERIFIED = "unverified"


def sign(secret: bytes, ip: str, *, now: float | None = None) -> str:
    ts = int(now if now is not None else time.time())
    mac = hmac.new(secret, f"{ts}|{ip}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{ts}.{ip}.{mac}"


def verified_ip(value: str | None, secret: bytes, *, now: float | None = None) -> str | None:
    """The signed address, or None when the header is absent, malformed, stale or does not verify."""
    if (
        not value or len(value) > 200 or not value.isascii()
    ):  # non-ASCII would raise in isdigit/int/compare_digest
        return None
    prefix, _, mac = value.rpartition(".")
    parts = prefix.split(".", 2)
    if len(parts) != 3 or parts[0] != "v1" or not parts[1].isdigit() or not parts[2]:
        return None
    ts, ip = int(parts[1]), parts[2]
    if abs((now if now is not None else time.time()) - ts) > MAX_SKEW_SECONDS:
        return None
    expected = hmac.new(secret, f"{ts}|{ip}".encode(), hashlib.sha256).hexdigest()
    return ip if hmac.compare_digest(expected, mac) else None
