#!/usr/bin/env python3
"""Select representative Potential Queries from the complete PQR50 pool.

The 43,200 generated Potential Queries remain an offline representation pool. The joint
evolution pipeline consumes one real Query nearest to each fitted PQR GMM mean.
This script uses only the generated Potential Queries and their embeddings.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from tqdm import tqdm


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--potential-queries", type=Path, required=True)
    parser.add_argument("--raw-embeddings", type=Path, required=True)
    parser.add_argument("--gmm-means", type=Path, required=True)
    parser.add_argument("--gmm-offsets", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows = read_jsonl(args.potential_queries)
    raw = np.load(args.raw_embeddings, mmap_mode="r")
    means = np.load(args.gmm_means, mmap_mode="r")
    offsets = np.load(args.gmm_offsets)
    if len(rows) != 864 or len(offsets) != 865:
        raise ValueError(f"expected 864 Skills and 865 offsets, got {len(rows)} and {len(offsets)}")
    queries_per_skill = len(rows[0]["potential_queries"])
    if queries_per_skill != 50 or len(raw) != len(rows) * queries_per_skill:
        raise ValueError("PQR50 shape mismatch")

    representative_rows: list[dict[str, Any]] = []
    components_per_skill: list[int] = []
    for skill_index, row in enumerate(tqdm(rows, desc="Select component medoids", unit="Skill")):
        skill_raw = np.asarray(raw[skill_index * queries_per_skill:(skill_index + 1) * queries_per_skill])
        skill_means = np.asarray(means[int(offsets[skill_index]):int(offsets[skill_index + 1])])
        components_per_skill.append(len(skill_means))
        similarities = skill_means @ skill_raw.T
        used_query_indices: set[int] = set()
        for component_index, scores in enumerate(similarities):
            # Keep component representatives distinct when possible.
            order = np.argsort(-scores, kind="stable")
            query_index = next((int(index) for index in order if int(index) not in used_query_indices), int(order[0]))
            used_query_indices.add(query_index)
            representative_rows.append({
                "potential_query_id": f"pqr-medoid-{skill_index:04d}-{component_index:02d}",
                "skill_index": skill_index,
                "skill_name": row["skill_name"],
                "component_index": component_index,
                "raw_query_index": query_index,
                "potential_query": row["potential_queries"][query_index],
                "component_medoid_cosine": round(float(scores[query_index]), 8),
                "role": "representative_potential_query",
            })

    args.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_jsonl(args.output_dir / "representative_potential_queries.jsonl", representative_rows)
    atomic_json(args.output_dir / "summary.json", {
        "status": "complete",
        "skill_count": len(rows),
        "offline_generated_goal_pool": len(raw),
        "representative_potential_query_count": len(representative_rows),
        "representative_potential_query_fraction": round(len(representative_rows) / len(raw), 8),
        "components_per_skill": {
            "min": min(components_per_skill),
            "max": max(components_per_skill),
            "mean": round(float(np.mean(components_per_skill)), 8),
            "histogram": {str(value): components_per_skill.count(value) for value in sorted(set(components_per_skill))},
        },
        "selection": "nearest distinct Potential Query to each normalized GMM component mean",
        "unselected_potential_query_count": len(raw) - len(representative_rows),
    })


if __name__ == "__main__":
    main()
