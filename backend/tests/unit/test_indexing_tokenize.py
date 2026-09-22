from __future__ import annotations

from unfold.indexing.tokenize import tokenize, tokenize_query


def test_camel_case_keeps_original_and_parts() -> None:
    assert tokenize("verifyAccessToken") == ["verifyaccesstoken", "verify", "access", "token"]


def test_snake_and_screaming_case() -> None:
    assert tokenize("MAX_RETRY_COUNT") == ["max_retry_count", "max", "retry", "count"]
    assert tokenize("get_user_by_id") == ["get_user_by_id", "get", "user", "id"]


def test_paths_dots_and_kebab() -> None:
    toks = tokenize("backend/auth/service.py")
    assert toks[0] == "backend/auth/service.py"
    assert {"backend", "auth", "service", "py"} <= set(toks)
    assert tokenize("my-cool-lib")[:1] == ["my-cool-lib"]
    assert {"my", "cool", "lib"} <= set(tokenize("my-cool-lib"))
    assert "app.core.config" in tokenize("from app.core.config import Settings")


def test_acronyms_split() -> None:
    assert tokenize("HTTPServerError") == ["httpservererror", "http", "server", "error"]


def test_stopwords_single_chars_and_numbers() -> None:
    toks = tokenize("the user is a 7 or 42 x in self")
    assert toks == ["user", "42"]


def test_query_dedupes_in_order() -> None:
    assert tokenize_query("token Token verifyToken") == ["token", "verifytoken", "verify"]
