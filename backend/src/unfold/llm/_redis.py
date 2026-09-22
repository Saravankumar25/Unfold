"""Typing shim: redis-py annotates async commands as `Awaitable[T] | T`."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable


async def resolve[T](value: Awaitable[T] | T) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
