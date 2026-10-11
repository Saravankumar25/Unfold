import json


def export(rows: list[dict[str, str]]) -> str:
    return json.dumps(rows)
