"""Capability → ordered (provider, model) candidates (LLD §13, HLD D1).

Gemini tiers come first (from settings via `GeminiProvider.models_for`); Groq models, when Groq keys exist, are
appended as extra fallbacks. Duplicates (tiers sharing a model) are removed; `exclude_models` is honoured.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from unfold.domain.enums import ModelCapability
from unfold.llm.providers.base import Provider

PROVIDER_ORDER: tuple[str, ...] = ("gemini", "groq")


@dataclass(frozen=True)
class Candidate:
    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class ModelRouter:
    def __init__(self, providers: Mapping[str, Provider], order: Iterable[str] = PROVIDER_ORDER) -> None:
        self._providers = dict(providers)
        known = [p for p in order if p in self._providers]
        self._order = known + sorted(p for p in self._providers if p not in known)

    @property
    def providers(self) -> list[str]:
        return list(self._order)

    def candidates(self, capability: ModelCapability, exclude_models: Iterable[str] = ()) -> list[Candidate]:
        excluded = set(exclude_models)
        out: list[Candidate] = []
        seen: set[tuple[str, str]] = set()
        for name in self._order:
            provider = self._providers[name]
            if provider.key_count <= 0:
                continue
            for model in provider.models_for(capability):
                key = (name, model)
                if key in seen or model in excluded or f"{name}:{model}" in excluded:
                    continue
                seen.add(key)
                out.append(Candidate(name, model))
        return out

    def long_context(self) -> set[Candidate]:
        return set(self.candidates(ModelCapability.LONG_CONTEXT))
