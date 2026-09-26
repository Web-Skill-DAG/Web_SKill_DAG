#!/usr/bin/env python3
"""Convert a Skill-DAG checkpoint into the recursive query router's index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def materialize(checkpoint: Path) -> dict[str, Any]:
    graph = json.loads((checkpoint / "graph.json").read_text(encoding="utf-8"))
    skills: dict[str, dict[str, Any]] = {}
    for line in (checkpoint / "skills.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            skill = json.loads(line)
            skills[skill["name"]] = skill
    nodes = graph["nodes"]
    roots = graph["root_node_names"]
    if not roots or not skills:
        raise ValueError("checkpoint must contain roots and Skills")
    cache: dict[str, set[str]] = {}
    visiting: set[str] = set()

    def reachable(name: str) -> set[str]:
        if name in cache:
            return cache[name]
        if name not in nodes:
            raise ValueError(f"unknown DAG node: {name}")
        if name in visiting:
            raise ValueError(f"DAG cycle at {name}")
        visiting.add(name)
        node = nodes[name]
        found = set(node.get("direct_skill_names", []))
        for child in node.get("child_names", []):
            found.update(reachable(child))
        visiting.remove(name)
        missing = found - set(skills)
        if missing:
            raise ValueError(f"unknown Skills below {name}: {sorted(missing)[:3]}")
        cache[name] = found
        return found

    categories = []
    for index, (name, node) in enumerate(nodes.items(), 1):
        categories.append({
            "category_id": f"dag-node-{index:04d}",
            "name": name,
            "description": node.get("description", ""),
            "parent_names": node.get("parent_names", []),
            "child_names": node.get("child_names", []),
            "direct_skill_names": node.get("direct_skill_names", []),
            "skills": [skills[skill] for skill in sorted(reachable(name))],
        })
    for root in roots:
        reachable(root)
    return {"categories": categories}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    value = materialize(args.checkpoint.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "category_skill_index.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Materialized {len(value['categories'])} nodes: {path}")


if __name__ == "__main__":
    main()
