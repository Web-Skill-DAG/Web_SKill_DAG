#!/usr/bin/env python3
"""Complete evidence-induced child partitions without imposing an online quota.

The sparse synthesis stage discovers child routing semantics from repeated
query evidence.  This stage assigns the parent's remaining direct Skills to
those discovered children in small, resumable batches.  Skills with no
material fit remain directly under the parent.  Candidate reduction therefore
comes from the learned structure, not a hard root or candidate top-k.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from tqdm import tqdm

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    PARTITION_ASSIGNMENT_PROMPT as ASSIGN_PROMPT,
)



HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common_openai_api as api

from common_checkpoint_io import atomic_json, atomic_jsonl, read_jsonl, usage


async def assign_batch(
    parent: dict[str, Any],
    children: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    record_dir: Path,
    args: argparse.Namespace,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    payload = {
        "parent_node": {"name": parent["name"], "description": parent["description"]},
        "child_nodes": [{"name": row["name"], "description": row["description"]} for row in children],
        "remaining_skills": [{"name": row["name"], "description": row["description"]} for row in skill_rows],
    }
    input_path = record_dir / "input.json"
    result_path = record_dir / "result.json"
    if args.resume and input_path.exists() and result_path.exists():
        if json.loads(input_path.read_text(encoding="utf-8")) == payload:
            return json.loads(result_path.read_text(encoding="utf-8"))
    record_dir.mkdir(parents=True, exist_ok=True)
    allowed_skills = {row["name"] for row in skill_rows}
    allowed_children = {row["name"] for row in children}
    prompt = ASSIGN_PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False)
    attempts: list[dict[str, Any]] = []
    correction = ""
    while True:
        try:
            async with semaphore:
                request, response, status, _ = await asyncio.to_thread(
                    api.call_chat_api, prompt + correction, args, str(uuid.uuid4())
                )
            text = api.response_text(response)
            value = api.extract_json_object(text)
            raw = value.get("assignments") if isinstance(value, dict) else None
            if not isinstance(raw, list):
                raise ValueError("assignments must be a list")
            clean = []
            seen: set[str] = set()
            for row in raw:
                if not isinstance(row, dict) or set(row) != {"skill_name", "child_names", "keep_at_parent", "reason"}:
                    raise ValueError("invalid assignment shape")
                skill_name = row["skill_name"]
                child_names = list(dict.fromkeys(row["child_names"])) if isinstance(row["child_names"], list) else []
                keep = row["keep_at_parent"]
                if skill_name not in allowed_skills or skill_name in seen:
                    raise ValueError("unknown or duplicate Skill")
                if not isinstance(keep, bool) or not set(child_names) <= allowed_children or len(child_names) > 2:
                    raise ValueError("invalid child selection")
                if keep == bool(child_names):
                    raise ValueError("exactly one of child assignment and keep_at_parent is required")
                seen.add(skill_name)
                clean.append({
                    "skill_name": skill_name,
                    "child_names": child_names,
                    "keep_at_parent": keep,
                    "reason": str(row["reason"]),
                })
            if seen != allowed_skills:
                raise ValueError(f"missing assignments: {sorted(allowed_skills - seen)}")
            result = {
                "parent_name": parent["name"],
                "assignments": clean,
                "usage": usage(response),
                "attempts": attempts + [{
                    "status": "accepted", "http_status": status, "request": request,
                    "response": response, "assistant_text": text,
                }],
            }
            atomic_json(input_path, payload)
            atomic_json(result_path, result)
            return result
        except Exception as error:
            attempts.append({"status": "failed", "error": f"{type(error).__name__}: {error}"})
            atomic_json(record_dir / "attempts.json", attempts)
            if not args.retry_forever and len(attempts) >= args.retries:
                raise
            correction = (
                f"\n\nPrevious output was invalid: {error}. "
                f"Valid child_names are exactly: {sorted(allowed_children)}. "
                f"Required skill_name values are exactly: {sorted(allowed_skills)}. "
                "Return each required Skill exactly once; use no other names; return corrected complete JSON only."
            )
            await asyncio.sleep(args.retry_delay)


async def main(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ValueError("QUERY_API_KEY, API_KEY, or --api-key is required")
    graph_path = args.checkpoint / "graph.json"
    skills_path = args.checkpoint / "skills.jsonl"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    skill_order = read_jsonl(skills_path)
    skills = {row["name"]: row for row in skill_order}
    semaphore = asyncio.Semaphore(args.concurrency)

    seed_results: list[dict[str, Any]] = []
    processed_by_parent: dict[str, set[str]] = {}
    if args.seed_assignment_results_dir:
        for result_path in sorted(args.seed_assignment_results_dir.glob("**/result.json")):
            result = json.loads(result_path.read_text(encoding="utf-8"))
            parent_name = result.get("parent_name")
            if parent_name not in graph["nodes"]:
                raise ValueError(f"seed assignment has unknown parent: {parent_name}")
            assigned = {row["skill_name"] for row in result.get("assignments", [])}
            unknown = assigned - set(skills)
            if unknown:
                raise ValueError(f"seed assignment has unknown Skills: {sorted(unknown)}")
            overlap = assigned & processed_by_parent.setdefault(parent_name, set())
            if overlap:
                raise ValueError(f"duplicate seeded assignments below {parent_name}: {sorted(overlap)}")
            processed_by_parent[parent_name].update(assigned)
            seed_results.append(result)

    work: list[tuple[str, int, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]] = []
    for parent_name, parent in graph["nodes"].items():
        if args.only_parent and parent_name not in set(args.only_parent):
            continue
        child_names = parent.get("child_names", [])
        already_processed = processed_by_parent.get(parent_name, set())
        remaining = [
            name for name in parent.get("direct_skill_names", [])
            if not skills[name].get("merged_into") and name not in already_processed
        ]
        if not child_names or not remaining:
            continue
        children = [graph["nodes"][name] for name in child_names]
        for index in range(0, len(remaining), args.batch_size):
            batch = [skills[name] for name in remaining[index:index + args.batch_size]]
            work.append((parent_name, index // args.batch_size, parent, children, batch))

    tasks = [
        asyncio.create_task(assign_batch(
            parent, children, batch,
            args.output_dir / "assignments" / parent_name / f"batch_{batch_index:03d}",
            args, semaphore,
        ))
        for parent_name, batch_index, parent, children, batch in work
    ]
    results = list(seed_results)
    bar = tqdm(total=len(tasks), desc="complete child partitions", unit="batch")
    for task in asyncio.as_completed(tasks):
        results.append(await task)
        bar.update(1)
    bar.close()

    next_graph = deepcopy(graph)
    edits: list[dict[str, Any]] = []
    for result in results:
        parent_name = result["parent_name"]
        for row in result["assignments"]:
            if row["keep_at_parent"]:
                continue
            skill_name = row["skill_name"]
            next_graph["nodes"][parent_name]["direct_skill_names"] = [
                name for name in next_graph["nodes"][parent_name]["direct_skill_names"] if name != skill_name
            ]
            for child_name in row["child_names"]:
                members = next_graph["nodes"][child_name].setdefault("direct_skill_names", [])
                if skill_name not in members:
                    members.append(skill_name)
                edits.append({
                    "operation": "complete_partition_rewire",
                    "remove_edge": {"node": parent_name, "skill": skill_name},
                    "add_edge": {"node": child_name, "skill": skill_name},
                    "reason": row["reason"],
                })

    next_graph["checkpoint"] = f"{graph['checkpoint']}_complete_partition"
    next_graph.setdefault("edit_history", []).append({
        "from_checkpoint": graph["checkpoint"],
        "to_checkpoint": next_graph["checkpoint"],
        "edits": edits,
    })
    checkpoint_dir = args.output_dir / "checkpoint"
    atomic_json(checkpoint_dir / "graph.json", next_graph)
    atomic_jsonl(checkpoint_dir / "skills.jsonl", skill_order)
    direct_by_split_parent = {
        name: len(node.get("direct_skill_names", []))
        for name, node in next_graph["nodes"].items() if node.get("child_names")
    }
    atomic_jsonl(args.output_dir / "assignment_results.jsonl", sorted(results, key=lambda row: row["parent_name"]))
    atomic_jsonl(checkpoint_dir / "edit_patch.jsonl", edits)
    atomic_json(checkpoint_dir / "summary.json", {
        "status": "complete",
        "checkpoint": next_graph["checkpoint"],
        "round": next_graph.get("round"),
        "assignment_batch_count": len(results),
        "seed_assignment_batch_count": len(seed_results),
        "new_assignment_batch_count": len(results) - len(seed_results),
        "rewired_skill_edge_count": len(edits),
        "direct_skill_counts_for_split_parents": direct_by_split_parent,
        "operation_counts": dict(Counter(row["operation"] for row in edits)),
        "assignment_input_tokens": sum(row["usage"]["input_tokens"] for row in results),
        "assignment_output_tokens": sum(row["usage"]["output_tokens"] for row in results),
    })
def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--checkpoint", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--batch-size", type=int, default=24)
    value.add_argument("--only-parent", action="append", default=[],
                       help="Complete partitions only below this parent (repeatable)")
    value.add_argument("--seed-assignment-results-dir", type=Path,
                       help="Reuse accepted result.json batches here and only request still-unprocessed Skills")
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--concurrency", type=int, default=200)
    value.add_argument("--request-concurrency", type=int, default=200)
    value.add_argument("--api-timeout", type=int, default=600)
    value.add_argument("--max-output-tokens", type=int, default=8192)
    value.add_argument("--transport-retries", type=int, default=3)
    value.add_argument("--transport-backoff-factor", type=float, default=0.5)
    value.add_argument("--max-global-backoff", type=float, default=120.0)
    value.add_argument("--retry-delay", type=float, default=5.0)
    value.add_argument("--retries", type=int, default=6)
    value.add_argument("--retry-forever", action=argparse.BooleanOptionalAction, default=True)
    value.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return value


if __name__ == "__main__":
    asyncio.run(main(parser().parse_args()))
