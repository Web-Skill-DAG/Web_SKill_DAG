#!/usr/bin/env python3
"""Diagnose retrieval failures and within-batch Skill relations.

The diagnosis is query-induced: it sees only the current checkpoint, one
representative Potential Query, its observed retrieval trace, and a small co-retrieved batch.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from tqdm import tqdm

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    DIAGNOSE_ROUTING_PROMPT as PROMPT,
)



HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common_openai_api as api


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


def memberships(graph: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for node_name, node in graph["nodes"].items():
        for skill_name in node.get("direct_skill_names", []):
            result[skill_name].append(node_name)
    return result


def validate(
    value: Any,
    skill_names: set[str],
    node_names: set[str],
    source_name: str,
    source_in_candidates: bool,
    source_rank: int | None,
    hit_k: int,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"miss_attribution", "relations"}:
        raise ValueError("top-level keys must be miss_attribution and relations")
    miss = value["miss_attribution"]
    if not isinstance(miss, dict) or set(miss) != {"stage", "operation", "target_name", "replacement_description", "reason"}:
        raise ValueError("invalid miss_attribution shape")
    stage = miss["stage"]
    operation = miss["operation"]
    if stage not in {"none", "root_route", "candidate_rank"}:
        raise ValueError("invalid stage")
    if operation not in {"none", "rewrite_skill", "rewrite_node", "add_edge"}:
        raise ValueError("invalid operation")
    if source_in_candidates and source_rank is not None and source_rank <= hit_k and operation != "none":
        raise ValueError(f"top-{hit_k} source must not receive a miss repair")
    if source_in_candidates and source_rank is not None and source_rank <= hit_k and stage != "none":
        raise ValueError(f"top-{hit_k} source must have none stage")
    if not source_in_candidates:
        if stage != "root_route" or operation not in {"none", "rewrite_node", "add_edge"}:
            raise ValueError("candidate miss must be repaired only at root-route boundary")
    elif source_rank is None or source_rank > hit_k:
        if stage != "candidate_rank" or operation not in {"none", "rewrite_skill"}:
            raise ValueError("ranking miss must be repaired only on the Skill side")
    target = str(miss["target_name"])
    replacement = str(miss["replacement_description"])
    if operation == "rewrite_skill" and (target != source_name or not replacement.strip()):
        raise ValueError("rewrite_skill must target source with replacement")
    if operation in {"rewrite_node", "add_edge"} and target not in node_names:
        raise ValueError("node operation target must be supplied node")
    if operation == "rewrite_node" and not replacement.strip():
        raise ValueError("rewrite_node requires replacement")
    if operation in {"none", "add_edge"} and replacement.strip():
        raise ValueError("none/add_edge replacement must be empty")

    raw_relations = value["relations"]
    if not isinstance(raw_relations, list):
        raise ValueError("relations must be a list")
    relations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw_relations:
        if not isinstance(item, dict) or set(item) != {"a", "b", "relation", "reason", "split_parent", "routing_distinction"}:
            raise ValueError("invalid relation shape")
        a, b = str(item["a"]), str(item["b"])
        relation = str(item["relation"])
        if a not in skill_names or b not in skill_names or a == b:
            raise ValueError("relation uses unknown/identical Skill")
        if relation not in {"same", "alternative_same_position"}:
            raise ValueError("invalid relation")
        pair = tuple(sorted((a, b)))
        signature = (pair[0], pair[1], relation)
        if signature in seen:
            continue
        seen.add(signature)
        split_parent = str(item["split_parent"])
        distinction = str(item["routing_distinction"])
        if relation == "same" and (split_parent or distinction):
            raise ValueError("same relation cannot contain split fields")
        if relation == "alternative_same_position" and (split_parent not in node_names or not distinction.strip()):
            raise ValueError("alternative relation requires supplied split parent and distinction")
        relations.append({
            "a": pair[0],
            "b": pair[1],
            "relation": relation,
            "reason": str(item["reason"]),
            "split_parent": split_parent,
            "routing_distinction": distinction,
        })
    return {
        "miss_attribution": {
            "stage": stage,
            "operation": operation,
            "target_name": target,
            "replacement_description": replacement,
            "reason": str(miss["reason"]),
        },
        "relations": relations,
    }


async def run_one(index: int, retrieval: dict[str, Any], graph: dict[str, Any], skills: dict[str, dict[str, Any]], member_map: dict[str, list[str]], args: argparse.Namespace, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    query_id = retrieval["query_id"]
    record_dir = args.output_dir / "records" / f"{index:04d}_{query_id}"
    result_path = record_dir / "result.json"
    if args.resume and result_path.exists():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("query_id") != query_id:
            raise ValueError(f"unsafe resume mismatch for {query_id}")
        return previous
    record_dir.mkdir(parents=True, exist_ok=True)

    source = retrieval["active_source_skill_name"]
    batch_names = [item["name"] for item in retrieval.get("ranking", [])[:args.batch_top_k]]
    if source not in batch_names:
        batch_names.append(source)
    supplied_skills = [
        {
            "name": name,
            "description": skills[name]["description"],
            "current_node_memberships": member_map.get(name, []),
            "observed_rank": next((item["rank"] for item in retrieval.get("ranking", []) if item["name"] == name), None),
        }
        for name in batch_names
    ]
    supplied_nodes = [
        {"name": name, "description": node["description"]}
        for name, node in graph["nodes"].items()
    ]
    payload = {
        "query_id": query_id,
        "potential_query": retrieval["potential_query"],
        "source_skill_name": source,
        "source_in_candidates": retrieval["source_in_candidates"],
        "source_rank": retrieval["source_rank"],
        "retention_top_k": args.hit_k,
        "selected_root_names": retrieval["selected_root_names"],
        "selected_leaf_node_names": retrieval["selected_leaf_node_names"],
        "supplied_nodes": supplied_nodes,
        "co_retrieved_skill_batch": supplied_skills,
    }
    prompt = PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False)
    attempts: list[dict[str, Any]] = []
    correction = ""
    while True:
        try:
            async with semaphore:
                request, response, status, _ = await asyncio.to_thread(
                    api.call_chat_api, prompt + correction, args, str(uuid.uuid4())
                )
            text = api.response_text(response)
            value = validate(
                api.extract_json_object(text), set(batch_names), set(graph["nodes"]), source,
                retrieval["source_in_candidates"], retrieval["source_rank"], args.hit_k,
            )
            result = {
                "query_id": query_id,
                "potential_query": retrieval["potential_query"],
                "source_skill_name": source,
                "checkpoint": graph["checkpoint"],
                **value,
                "usage": usage(response),
                "attempts": attempts + [{
                    "status": "accepted",
                    "http_status": status,
                    "request": request,
                    "response": response,
                    "assistant_text": text,
                }],
            }
            atomic_json(record_dir / "input.json", payload)
            atomic_json(result_path, result)
            return result
        except Exception as error:
            attempts.append({"status": "failed", "error": f"{type(error).__name__}: {error}"})
            if not args.retry_forever and len(attempts) >= args.retries:
                raise
            correction = f"\n\nPrevious output was invalid: {error}. Return corrected JSON only."
            await asyncio.sleep(args.retry_delay)


async def main(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ValueError("QUERY_API_KEY, API_KEY, or --api-key is required")
    graph = json.loads((args.checkpoint / "graph.json").read_text(encoding="utf-8"))
    skills = {row["name"]: row for row in read_jsonl(args.checkpoint / "skills.jsonl")}
    rows = read_jsonl(args.retrieval_results)
    if args.limit > 0:
        rows = rows[args.start:args.start + args.limit]
    elif args.start:
        rows = rows[args.start:]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    member_map = memberships(graph)
    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        asyncio.create_task(run_one(index, row, graph, skills, member_map, args, semaphore))
        for index, row in enumerate(rows, 1)
    ]
    results: list[dict[str, Any]] = []
    bar = tqdm(total=len(tasks), desc=f"diagnose {graph['checkpoint']}", unit="query")
    for task in asyncio.as_completed(tasks):
        results.append(await task)
        bar.update(1)
    bar.close()
    order = {row["query_id"]: index for index, row in enumerate(rows)}
    results.sort(key=lambda item: order[item["query_id"]])
    atomic_jsonl(args.output_dir / "diagnosis_results.jsonl", results)
    operations: dict[str, int] = defaultdict(int)
    relations: dict[str, int] = defaultdict(int)
    total_input = total_output = total_cost = 0.0
    for row in results:
        operations[row["miss_attribution"]["operation"]] += 1
        for relation in row["relations"]:
            relations[relation["relation"]] += 1
        total_input += row["usage"]["input_tokens"]
        total_output += row["usage"]["output_tokens"]
        total_cost += row["usage"]["cost"]
    n = len(results)
    atomic_json(args.output_dir / "summary.json", {
        "status": "complete",
        "checkpoint": graph["checkpoint"],
        "query_count": n,
        "operation_counts": dict(sorted(operations.items())),
        "relation_counts": dict(sorted(relations.items())),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "mean_input_tokens": total_input / n,
        "mean_output_tokens": total_output / n,
        "cost": total_cost,
        "offline_evolution_cost": True,
        "batch_top_k": args.batch_top_k,
        "hit_k": args.hit_k,
    })
def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--checkpoint", type=Path, required=True)
    value.add_argument("--retrieval-results", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--batch-top-k", type=int, default=12)
    value.add_argument("--hit-k", type=int, default=10)
    value.add_argument("--start", type=int, default=0)
    value.add_argument("--limit", type=int, default=0)
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--concurrency", type=int, default=50)
    value.add_argument("--request-concurrency", type=int, default=50)
    value.add_argument("--api-timeout", type=int, default=1800)
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
