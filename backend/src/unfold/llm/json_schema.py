"""JSON-Schema normalisation for provider structured output / tool parameters (LLD §13).

Pydantic's `model_json_schema()` emits `$defs`/`$ref`, `allOf` wrappers, `default`, `examples`, `const`, … that
provider schema dialects reject or ignore. `to_provider_schema` inlines refs (recursive refs collapse to a plain
object after `max_depth`) and keeps only a portable keyword subset.
"""

from __future__ import annotations

import copy
from typing import Any

# Keywords accepted by Gemini `response_json_schema` / `parameters_json_schema` and OpenAI-compatible tools.
_ALLOWED = frozenset(
    {
        "type",
        "format",
        "title",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "anyOf",
        "properties",
        "additionalProperties",
        "required",
        "propertyOrdering",
        "nullable",
    }
)


def _resolve_ref(ref: str, defs: dict[str, Any]) -> tuple[str, Any] | None:
    for prefix in ("#/$defs/", "#/definitions/"):
        if ref.startswith(prefix):
            name = ref[len(prefix) :]
            if name in defs:
                return name, defs[name]
    return None


def _convert(node: Any, defs: dict[str, Any], stack: tuple[str, ...], max_depth: int) -> Any:
    if isinstance(node, list):
        return [_convert(x, defs, stack, max_depth) for x in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        resolved = _resolve_ref(str(node["$ref"]), defs)
        if resolved is None:
            return {"type": "object"}
        name, target = resolved
        if stack.count(name) >= max_depth:
            # Recursive model: stop expanding, keep a permissive object so the schema stays finite.
            return {"type": "object", "description": f"{name} (nested)"}
        with_ref = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return _convert(with_ref, defs, (*stack, name), max_depth)

    # allOf with a single member is how pydantic attaches descriptions to refs.
    if "allOf" in node:
        members = node["allOf"]
        base = {k: v for k, v in node.items() if k != "allOf"}
        if isinstance(members, list) and len(members) == 1:
            member = _convert(members[0], defs, stack, max_depth)
            return _convert({**member, **base}, defs, stack, max_depth)
        merged: dict[str, Any] = {}
        props: dict[str, Any] = {}
        required: list[str] = []
        for m in members if isinstance(members, list) else []:
            cm = _convert(m, defs, stack, max_depth)
            if isinstance(cm, dict):
                props.update(cm.get("properties", {}))
                required.extend(r for r in cm.get("required", []) if r not in required)
                merged.update({k: v for k, v in cm.items() if k not in ("properties", "required")})
        if props:
            merged["properties"] = props
        if required:
            merged["required"] = required
        return _convert({**merged, **base}, defs, stack, max_depth)

    if "oneOf" in node and "anyOf" not in node:
        node = {**{k: v for k, v in node.items() if k != "oneOf"}, "anyOf": node["oneOf"]}
    if "const" in node and "enum" not in node:
        node = {**{k: v for k, v in node.items() if k != "const"}, "enum": [node["const"]]}

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {pk: _convert(pv, defs, stack, max_depth) for pk, pv in value.items()}
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            out[key] = _convert(value, defs, stack, max_depth)
        elif key in ("anyOf", "prefixItems") and isinstance(value, list):
            out[key] = [_convert(v, defs, stack, max_depth) for v in value]
        elif key == "enum" and isinstance(value, list):
            out[key] = list(value)
        else:
            out[key] = copy.deepcopy(value)
    if "properties" in out and "type" not in out:
        out["type"] = "object"
    if isinstance(out.get("required"), list) and isinstance(out.get("properties"), dict):
        out["required"] = [r for r in out["required"] if r in out["properties"]]
    return out


def to_provider_schema(schema: dict[str, Any], *, max_depth: int = 2) -> dict[str, Any]:
    """Return a self-contained (no `$ref`) schema restricted to the portable keyword subset."""
    defs: dict[str, Any] = {}
    defs.update(schema.get("definitions", {}) or {})
    defs.update(schema.get("$defs", {}) or {})
    root = {k: v for k, v in schema.items() if k not in ("$defs", "definitions", "$schema", "$id")}
    converted = _convert(root, defs, (), max_depth)
    if not isinstance(converted, dict):
        return {"type": "object"}
    if "type" not in converted and "anyOf" not in converted:
        converted["type"] = "object"
    return converted
