"""Checkpoint JSON/JSONL helpers shared by DAG edit steps."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def usage(response: dict[str, Any]) -> dict[str, float]:
    values = response.get("usage") or {}
    return {
        "input_tokens": float(values.get("prompt_tokens") or 0),
        "output_tokens": float(values.get("completion_tokens") or 0),
        "cost": float(values.get("cost") or 0),
        "kiro_credits": float(values.get("kiro_credits") or 0),
    }
