#!/usr/bin/env python3
"""Run the shared Router -> full-document Reviewer pipeline on a Skill DAG."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import uuid
from pathlib import Path
from typing import Any

from tqdm import tqdm


HERE = Path(__file__).resolve().parent
RELEASE_CODE = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(RELEASE_CODE))

import common_openai_api as api
from common_checkpoint_io import atomic_json, atomic_jsonl, read_jsonl
from dag_retrieval import active_skill_name, retrieve_skills


def response_usage(response: dict[str, Any]) -> dict[str, float]:
    values = response.get("usage") or {}
    return {
        "input_tokens": float(values.get("prompt_tokens") or 0),
        "output_tokens": float(values.get("completion_tokens") or 0),
        "cost": float(values.get("cost") or 0),
        "kiro_credits": float(values.get("kiro_credits") or 0),
    }


async def request_json(
    prompt: str,
    validator: Any,
    phase: str,
    args: argparse.Namespace,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    correction = ""
    while True:
        request: dict[str, Any] | None = None
        response: dict[str, Any] | None = None
        status: int | None = None
        try:
            async with semaphore:
                request, response, status, _ = await asyncio.to_thread(
                    api.call_chat_api,
                    prompt + correction,
                    args,
                    str(uuid.uuid4()),
                )
            assistant_text = api.response_text(response)
            value = validator(api.extract_json_object(assistant_text))
            accepted_usage = response_usage(response)
            trace = {
                "phase": phase,
                "usage": {
                    key: accepted_usage[key]
                    + sum((item.get("usage") or {}).get(key, 0.0) for item in attempts)
                    for key in ("input_tokens", "output_tokens", "cost", "kiro_credits")
                },
                "attempts": attempts + [{
                    "status": "accepted",
                    "http_status": status,
                    "request": request,
                    "response": response,
                    "assistant_text": assistant_text,
                }],
            }
            return value, trace
        except Exception as error:
            failed: dict[str, Any] = {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
            if response is not None:
                failed.update({
                    "http_status": status,
                    "request": request,
                    "response": response,
                    "assistant_text": api.response_text(response),
                    "usage": response_usage(response),
                })
            attempts.append(failed)
            if not args.retry_forever and len(attempts) >= args.retries:
                raise
            correction = f"\n\nPrevious output was invalid: {error}. Return corrected JSON only."
            await asyncio.sleep(args.retry_delay)


def trace_usage(result: dict[str, Any]) -> dict[str, float]:
    traces = [item["trace"] for item in result["routing_traces"]]
    traces.extend(result["review_traces"])
    return {
        key: sum((trace.get("usage") or {}).get(key, 0.0) for trace in traces)
        for key in ("input_tokens", "output_tokens", "cost", "kiro_credits")
    }


async def run_one(
    index: int,
    row: dict[str, Any],
    graph: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    args: argparse.Namespace,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    query_id = row.get("potential_query_id")
    if not query_id:
        raise ValueError("query row needs potential_query_id")
    potential_query = str(row["potential_query"]).strip()
    record_dir = args.output_dir / "records" / f"{index:04d}_{query_id}"
    result_path = record_dir / "result.json"
    if args.resume and result_path.exists():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("query_id") != query_id or previous.get("potential_query") != potential_query:
            raise ValueError(f"unsafe resume mismatch for {query_id}")
        return previous
    record_dir.mkdir(parents=True, exist_ok=True)

    async def call(prompt: str, validator: Any, phase: str):
        return await request_json(prompt, validator, phase, args, semaphore)

    retrieval = await retrieve_skills(
        potential_query,
        graph,
        skills,
        call,
        review_batch_size=args.review_batch_size,
        top_k=args.top_k,
    )
    source_original = str(row["skill_name"])
    source_active = active_skill_name(source_original, skills)
    ranked_names = [item["name"] for item in retrieval["ranking"]]
    rank = ranked_names.index(source_active) + 1 if source_active in ranked_names else None
    candidate_names = retrieval["candidate_skill_names"]
    usage = trace_usage(retrieval)
    result = {
        "query_id": query_id,
        "potential_query": potential_query,
        "source_skill_name": source_original,
        "active_source_skill_name": source_active,
        "checkpoint": graph.get("checkpoint", args.checkpoint.name),
        "selected_root_names": retrieval["selected_root_names"],
        "selected_leaf_node_names": retrieval["selected_leaf_node_names"],
        "selected_node_names": retrieval["selected_node_names"],
        "candidate_skill_names": candidate_names,
        "candidate_skill_count": len(candidate_names),
        "source_in_candidates": source_active in set(candidate_names),
        "review_decisions": retrieval["decisions"],
        "ranking": retrieval["ranking"],
        "source_rank": rank,
        "source_hit_at_1": rank == 1,
        "source_hit_at_5": rank is not None and rank <= 5,
        "source_hit_at_10": rank is not None and rank <= 10,
        "usage": usage,
        "routing_traces": retrieval["routing_traces"],
        "review_traces": retrieval["review_traces"],
    }
    atomic_json(record_dir / "input.json", row)
    atomic_json(result_path, result)
    return result


async def main(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ValueError("QUERY_API_KEY, API_KEY, or --api-key is required")
    graph = json.loads((args.checkpoint / "graph.json").read_text(encoding="utf-8"))
    skill_rows = read_jsonl(args.checkpoint / "skills.jsonl")
    skills = {row["name"]: row for row in skill_rows}
    rows = read_jsonl(args.queries)
    if args.limit > 0:
        rows = rows[args.start:args.start + args.limit]
    elif args.start:
        rows = rows[args.start:]
    if not rows:
        raise ValueError("no representative Potential Queries selected")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [
        asyncio.create_task(run_one(index, row, graph, skills, args, semaphore))
        for index, row in enumerate(rows, 1)
    ]
    results: list[dict[str, Any]] = []
    bar = tqdm(total=len(tasks), desc=f"retrieve {graph.get('checkpoint', args.checkpoint.name)}", unit="query")
    for task in asyncio.as_completed(tasks):
        results.append(await task)
        bar.update(1)
    bar.close()
    order = {
        row["potential_query_id"]: index
        for index, row in enumerate(rows)
    }
    results.sort(key=lambda item: order[item["query_id"]])
    atomic_jsonl(args.output_dir / "retrieval_results.jsonl", results)
    n = len(results)
    total_usage = {
        key: sum(row["usage"][key] for row in results)
        for key in ("input_tokens", "output_tokens", "cost", "kiro_credits")
    }
    atomic_json(args.output_dir / "summary.json", {
        "status": "complete",
        "checkpoint": graph.get("checkpoint", args.checkpoint.name),
        "query_count": n,
        "source_candidate_coverage": sum(row["source_in_candidates"] for row in results) / n,
        "source_r1": sum(row["source_hit_at_1"] for row in results) / n,
        "source_r5": sum(row["source_hit_at_5"] for row in results) / n,
        "source_r10": sum(row["source_hit_at_10"] for row in results) / n,
        "source_mrr10": sum(
            1.0 / row["source_rank"]
            if row["source_rank"] and row["source_rank"] <= 10 else 0.0
            for row in results
        ) / n,
        "candidate_skill_count_mean": statistics.mean(row["candidate_skill_count"] for row in results),
        "candidate_skill_count_median": statistics.median(row["candidate_skill_count"] for row in results),
        "input_tokens": total_usage["input_tokens"],
        "output_tokens": total_usage["output_tokens"],
        "mean_input_tokens": total_usage["input_tokens"] / n,
        "mean_output_tokens": total_usage["output_tokens"] / n,
        "cost": total_usage["cost"],
        "retrieval_pipeline": "shared_recursive_router_and_full_document_reviewer",
        "top_k": args.top_k,
        "review_batch_size": args.review_batch_size,
        "offline_evolution_run": True,
    })
def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--checkpoint", type=Path, required=True)
    value.add_argument("--queries", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--start", type=int, default=0)
    value.add_argument("--limit", type=int, default=0)
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--concurrency", type=int, default=50)
    value.add_argument("--request-concurrency", type=int, default=50)
    value.add_argument("--top-k", type=int, default=10)
    value.add_argument("--review-batch-size", type=int, default=8)
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
