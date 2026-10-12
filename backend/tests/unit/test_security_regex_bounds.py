from __future__ import annotations

import time

from unfold.exports.render import sanitize_mermaid
from unfold.security.fence import strip_fence_tags
from unfold.security.secrets import redact


def _seconds(fn: object, text: str) -> float:
    start = time.perf_counter()
    fn(text)  # type: ignore[operator]
    return time.perf_counter() - start


def test_redact_is_linear_on_one_long_token_without_a_keyword() -> None:
    assert _seconds(redact, "a" * 50_000) < 3.0  # was minutes (33 s at 10 000 characters)


def test_fence_stripper_is_linear_on_repeated_unclosed_tag_openers() -> None:
    assert _seconds(strip_fence_tags, "<repository_data" * 20_000) < 2.0  # was quadratic


def test_mermaid_directives_cannot_be_rebuilt_from_the_text_around_a_removed_one() -> None:
    out = sanitize_mermaid('A-->B %%%%{x}%%{init:{"themeCSS":"a{}"}}%%')
    assert "%%{" not in out


def test_a_long_or_unclosed_fence_tag_opener_is_still_removed() -> None:
    assert "<repository_data" not in strip_fence_tags("x <repository_data " + "a" * 500 + "> y")
    assert "<repository_data" not in strip_fence_tags("<repository_data source=")


def test_mermaid_front_matter_and_directives_cannot_be_rebuilt_in_any_order() -> None:
    for src in ("%%%%{init}%%{init:{}}%%", "---\n---\n%%{init:{}}%%", "A-->B %%%%{{x", "%%{unclosed\nA-->B"):
        out = sanitize_mermaid(src)
        assert "%%{" not in out and not out.startswith("---"), (src, out)


def test_mermaid_sanitizer_is_fast_on_a_directive_ladder() -> None:
    assert _seconds(sanitize_mermaid, "%%" * 10_000 + "{" * 10_000) < 2.0
