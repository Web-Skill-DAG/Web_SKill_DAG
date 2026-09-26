#!/usr/bin/env python3
"""Encode generated Potential Queries and fit per-Skill GMMs for representative selection."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.mixture import GaussianMixture
from tqdm import tqdm


from embedding_encoder import (
    DEFAULT_QUERY_INSTRUCTION,
    encode_texts,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_save_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)
    os.replace(temporary, path)


def normalize_rows(value: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(value, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("zero-norm GMM mean")
    return (value / norms).astype(np.float32)


def cosine_diversity(embeddings: np.ndarray) -> dict[str, float]:
    similarities = embeddings @ embeddings.T
    upper = similarities[np.triu_indices(len(embeddings), 1)]
    return {
        "mean_pairwise_cosine": round(float(upper.mean()), 6),
        "median_pairwise_cosine": round(float(np.median(upper)), 6),
        "p95_pairwise_cosine": round(float(np.quantile(upper, 0.95)), 6),
        "max_pairwise_cosine": round(float(upper.max()), 6),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--potential-queries", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--embedding-encoder", default=os.environ.get("EMBEDDING_ENCODER", ""))
    parser.add_argument("--embedding-revision", default=os.environ.get("EMBEDDING_REVISION", ""))
    parser.add_argument("--query-instruction", default=DEFAULT_QUERY_INSTRUCTION)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--queries-per-skill", type=int, default=50)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--min-components", type=int, default=2)
    parser.add_argument("--max-components", type=int, default=6)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=50)
    parser.add_argument("--reg-covar", type=float, default=1e-6)
    args = parser.parse_args()
    if not args.embedding_encoder:
        raise SystemExit("--embedding-encoder or EMBEDDING_ENCODER is required")
    if (args.min_components, args.max_components) != (2, 6):
        raise SystemExit("the evolution protocol requires GMM K=2..6")

    rows = read_jsonl(args.potential_queries)
    if not rows or len({row["skill_key"] for row in rows}) != len(rows):
        raise ValueError("Potential-Query rows must contain unique skill_key values")
    counts = {len(row["potential_queries"]) for row in rows}
    if counts != {args.queries_per_skill}:
        raise ValueError(f"expected {args.queries_per_skill} Potential Queries per Skill, found {sorted(counts)}")
    flat_queries = [query for row in rows for query in row["potential_queries"]]
    started = time.time()
    embeddings = encode_texts(
        flat_queries,
        encoder_path=args.embedding_encoder,
        is_query=True,
        batch_size=args.batch_size,
        max_length=args.max_length,
        device=args.device,
        revision=args.embedding_revision,
        instruction=args.query_instruction,
    )
    expected_shape = (len(rows) * args.queries_per_skill, 1024)
    if embeddings.shape != expected_shape:
        raise ValueError(f"embedding shape {embeddings.shape}, expected {expected_shape}")
    norms = np.linalg.norm(embeddings, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-5):
        raise ValueError(f"embeddings are not unit normalized: {norms.min()}..{norms.max()}")

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    atomic_save_npy(output / "raw_potential_query_embeddings.npy", embeddings)

    all_means: list[np.ndarray] = []
    offsets = [0]
    gmm_rows: list[dict[str, Any]] = []
    for skill_index, row in enumerate(tqdm(rows, desc="Fit diagonal GMM", unit="skill")):
        start = skill_index * args.queries_per_skill
        skill_embeddings = embeddings[start : start + args.queries_per_skill]
        candidates: list[dict[str, Any]] = []
        best: tuple[float, GaussianMixture] | None = None
        for components in range(args.min_components, args.max_components + 1):
            gmm = GaussianMixture(
                n_components=components,
                covariance_type="diag",
                random_state=args.random_state,
                max_iter=args.max_iter,
                reg_covar=args.reg_covar,
                n_init=1,
            ).fit(skill_embeddings)
            bic = float(gmm.bic(skill_embeddings))
            candidates.append({
                "components": components,
                "bic": round(bic, 6),
                "converged": bool(gmm.converged_),
                "iterations": int(gmm.n_iter_),
            })
            if best is None or bic < best[0]:
                best = (bic, gmm)
        assert best is not None
        chosen = best[1]
        means = normalize_rows(chosen.means_)
        all_means.append(means)
        offsets.append(offsets[-1] + len(means))
        gmm_rows.append({
            "skill_key": row["skill_key"],
            "skill_name": row["skill_name"],
            "selected_components": int(chosen.n_components),
            "selected_bic": round(float(best[0]), 6),
            "selected_converged": bool(chosen.converged_),
            "selected_iterations": int(chosen.n_iter_),
            "candidate_fits": candidates,
            "raw_embedding_diversity": cosine_diversity(skill_embeddings),
        })

    means_array = np.concatenate(all_means, axis=0).astype(np.float32)
    atomic_save_npy(output / "gmm_normalized_means.npy", means_array)
    atomic_save_npy(output / "gmm_mean_offsets.npy", np.asarray(offsets, dtype=np.int64))

    k_counts = Counter(row["selected_components"] for row in gmm_rows)
    converged = sum(row["selected_converged"] for row in gmm_rows)
    diversity_means = [row["raw_embedding_diversity"]["mean_pairwise_cosine"] for row in gmm_rows]
    summary = {
        "status": "complete",
        "skills": len(rows),
        "potential_queries": len(flat_queries),
        "embedding_dimension": int(embeddings.shape[1]),
        "embedding_encoder": args.embedding_encoder,
        "embedding_revision": args.embedding_revision,
        "elapsed_seconds": round(time.time() - started, 3),
        "gmm_selected_k_distribution": dict(sorted(k_counts.items())),
        "gmm_converged": converged,
        "gmm_not_converged": len(rows) - converged,
        "mean_within_skill_pairwise_cosine": round(float(np.mean(diversity_means)), 6),
        "raw_vectors": int(embeddings.shape[0]),
        "gmm_mean_vectors": int(len(means_array)),
        "compression_ratio": round(float(len(means_array) / len(embeddings)), 6),
    }
    atomic_write_json(output / "summary.json", summary)
    parameters = vars(args).copy()
    parameters.update({"potential_queries": str(args.potential_queries), "output_dir": str(args.output_dir)})
    atomic_write_json(output / "run_manifest.json", {
        "potential_queries": str(args.potential_queries.resolve()),
        "parameters": parameters,
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
