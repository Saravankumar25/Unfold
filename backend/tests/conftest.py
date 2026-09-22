from __future__ import annotations

import os

# Tests must never read a developer's real .env keys or talk to real providers unless marked `llm`.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_FORMAT", "console")
