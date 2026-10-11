def export(rows: list[dict[str, str]]) -> str:
    return "\n".join(",".join(r.values()) for r in rows)
