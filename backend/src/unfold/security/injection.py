"""Rule-based prompt-injection scanner (LLD §6, AI infra §40). Signals label trust; they never block analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass

_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|all|earlier|system)\b"
            r"[^.\n]{0,20}\b(instructions?|prompts?|rules?|messages?)",
            re.I,
        ),
    ),
    ("role_reassignment", re.compile(r"\b(you are now|act as|pretend to be|from now on you)\b", re.I)),
    (
        "system_prompt_probe",
        re.compile(r"\b(reveal|print|show|repeat)\b[^.\n]{0,30}\b(system prompt|instructions)\b", re.I),
    ),
    (
        "chat_template_tokens",
        re.compile(r"<\|(im_start|im_end|system|assistant|user|endoftext)\|>|\[/?INST\]|<<SYS>>", re.I),
    ),
    ("fake_role_header", re.compile(r"^\s*(system|assistant)\s*:\s", re.I | re.M)),
    ("tool_call_imitation", re.compile(r"\"(tool_calls|function_call)\"\s*:", re.I)),
    (
        "exfiltration",
        re.compile(
            r"\b(send|post|upload|exfiltrate)\b[^.\n]{0,40}\b(api[ _-]?key|secret|token|credentials?)\b", re.I
        ),
    ),
    ("agent_directive_file", re.compile(r"\b(AGENTS|CLAUDE|GEMINI|COPILOT)\.md\b")),
]


@dataclass(frozen=True)
class InjectionSignal:
    rule: str
    line: int
    excerpt: str


def scan(text: str, max_signals: int = 20) -> list[InjectionSignal]:
    signals: list[InjectionSignal] = []
    for rule, pattern in _RULES:
        for m in pattern.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            signals.append(InjectionSignal(rule, line, m.group(0)[:80]))
            if len(signals) >= max_signals:
                return signals
    return signals


def trust_label(signals: list[InjectionSignal]) -> str:
    """`untrusted_source` for all repository data; `suspicious` when injection-like content was found."""
    strong = {"override_instructions", "chat_template_tokens", "exfiltration", "role_reassignment"}
    return "suspicious" if any(s.rule in strong for s in signals) else "untrusted_source"
