#!/usr/bin/env python3
"""Expand Queries from retrieved Categories and full Skills using direct API calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from tqdm import tqdm

import common_model_api as direct_api
import common_io as common

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import CONSISTENCY_PROMPT, QUERY_EXPANSION_PROMPT
from dag_retrieval import retrieve_skills



def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return [nonempty(item, f"{field}[{index}]") for index, item in enumerate(value)]

def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not str(row.get("data_id", "")).strip():
                raise ValueError(f"line {line_number} has no data_id")
            if not str(row.get("query", "")).strip():
                raise ValueError(f"line {line_number} has no query")
            rows.append(row)
    return rows


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    """Replace one JSONL atomically so concurrent readers never see a partial line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def materialize_incremental_jsonl(
    output_root: Path,
    completed: dict[int, dict[str, Any]],
) -> int:
    """Publish every completed case immediately, in stable source order."""
    ordered = [completed[position] for position in sorted(completed)]
    write_jsonl_atomic(
        output_root / "expanded_queries.jsonl",
        [item["expanded_row"] for item in ordered],
    )
    write_jsonl_atomic(
        output_root / "expansion_audit.jsonl",
        [
            {key: value for key, value in item.items() if key != "expanded_row"}
            for item in ordered
        ],
    )
    return len(ordered)


def query_expansion_validator(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"query"}:
        raise ValueError("Query expansion must contain only query")
    return {"query": nonempty(value["query"], "query")}


async def expand_via_direct_api(
    position: int,
    query: str,
    selected_skills: list[dict[str, Any]],
    review_decisions: list[dict[str, Any]],
    record: Path,
    semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_names = [item["name"] for item in selected_skills]
    skill_decisions = [{
        "name": item["name"],
        "decision": "accept" if item["applicable"] and item["name"] in selected_names else "reject",
        "reason": item["reason"],
    } for item in review_decisions]
    if not selected_skills:
        return {
            "skill_decisions": skill_decisions,
            "selected_skill_names": [],
            "query": "",
        }, {"skipped": "no applicable Skills"}

    prompt = (
        QUERY_EXPANSION_PROMPT
        + "\n\nInput:\n"
        + json.dumps({
            "query": query,
            "reviewed_skill_documents": selected_skills,
        }, ensure_ascii=False, indent=2)
    )
    result_path = record / "query_expansion.json"
    trajectory_path = record / "query_expansion_trajectory.json"
    if args.resume and await case_io(args, result_path.is_file):
        expansion = query_expansion_validator(await read_json_io(args, result_path))
        trajectory = (
            await read_json_io(args, trajectory_path)
            if await case_io(args, trajectory_path.is_file)
            else {"resumed": True}
        )
        return {
            "skill_decisions": skill_decisions,
            "selected_skill_names": selected_names,
            "query": expansion["query"],
        }, trajectory

    async with semaphore:
        expansion, trajectory = await direct_api.retry_direct_dialogue(
            prompt,
            "query_expansion",
            f"query-{position:04d}",
            query_expansion_validator,
            args,
        )
    await asyncio.gather(
        write_json_io(args, result_path, expansion),
        write_json_io(args, trajectory_path, trajectory),
    )
    return {
        "skill_decisions": skill_decisions,
        "selected_skill_names": selected_names,
        "query": expansion["query"],
    }, trajectory


def consistency_validator(raw_expanded_query: str):
    def validate(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"intent_consistent", "issues", "final_query"}:
            raise ValueError("Consistency review has invalid fields")
        if not isinstance(value["intent_consistent"], bool):
            raise ValueError("intent_consistent must be a boolean")
        issues = string_list(value["issues"], "issues")
        if not isinstance(value["final_query"], str):
            raise ValueError("final_query must be a string")
        final_query = value["final_query"].strip()
        if value["intent_consistent"]:
            if issues:
                raise ValueError("a consistent expansion must have no issues")
            if final_query != raw_expanded_query:
                # Some compatible endpoints preserve the content but
                # normalize whitespace or punctuation in the
                # echoed final_query.  In this opt-in mode, retain the
                # already-generated expansion verbatim rather than spending
                # infinite retries on an otherwise consistent review.
                if common.env_bool("ALLOW_CONSISTENCY_NORMALIZATION", False):
                    final_query = raw_expanded_query
                else:
                    raise ValueError("a consistent expansion must be preserved verbatim")
        elif not issues:
            raise ValueError("an inconsistent expansion must identify at least one issue")
        return {
            "intent_consistent": value["intent_consistent"],
            "issues": issues,
            "final_query": final_query,
        }
    return validate


def build_generation_query(original_query: str, expanded_query: str) -> str:
    """Keep the user's Query verbatim and append only the selected visual requirements."""
    expanded_query = expanded_query.strip()
    if not expanded_query:
        return original_query.rstrip()
    return original_query.rstrip() + "\n\n" + expanded_query


async def apply_consistency_review(
    position: int,
    query: str,
    raw_expanded_query: str,
    record: Path,
    semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    review_path = record / "consistency_review.json"
    trajectory_path = record / "consistency_trajectory.json"
    validator = consistency_validator(raw_expanded_query)
    if args.resume and await case_io(args, review_path.is_file):
        saved = await read_json_io(args, review_path)
        review = validator(saved)
        trajectory = (
            await read_json_io(args, trajectory_path)
            if await case_io(args, trajectory_path.is_file)
            else {"resumed": True}
        )
        return review, trajectory

    prompt = (
        CONSISTENCY_PROMPT
        + "\n\nOriginal Query:\n"
        + json.dumps({"query": query}, ensure_ascii=False, indent=2)
        + "\n\nAppended visual-effects section:\n"
        + json.dumps({"query": raw_expanded_query}, ensure_ascii=False, indent=2)
    )
    async with semaphore:
        review, trajectory = await direct_api.retry_direct_dialogue(
            prompt, "intent_consistency", f"query-{position:04d}", validator, args
        )
    await asyncio.gather(
        write_json_io(args, review_path, review),
        write_json_io(args, trajectory_path, trajectory),
    )
    return review, trajectory


async def finalize_result(
    result: dict[str, Any],
    source_row: dict[str, Any],
    record: Path,
    consistency_semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> dict[str, Any]:
    raw_expanded_query = str(
        result.get("skill_expanded_query", result.get("expanded_query", ""))
    ).strip()
    if raw_expanded_query:
        review, _ = await apply_consistency_review(
            int(result["query_index"]), str(result["original_query"]), raw_expanded_query,
            record, consistency_semaphore, args,
        )
    else:
        review = {"intent_consistent": True, "issues": [], "final_query": ""}
    final_query = review["final_query"]
    expanded_row = dict(source_row)
    expanded_row["original_query"] = str(result["original_query"])
    expanded_row["expanded_query"] = final_query
    expanded_row["query"] = build_generation_query(str(result["original_query"]), final_query)
    result.update({
        "skill_expanded_query": raw_expanded_query,
        "expanded_query": final_query,
        "consistency_review": {
            "intent_consistent": review["intent_consistent"],
            "issues": review["issues"],
        },
        "consistency_changed": final_query != raw_expanded_query,
        "expanded_row": expanded_row,
    })
    await write_json_io(args, record / "result.json", result)
    return result


def safe_name(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip()).strip("._-")
    return text[:120] or "skill"


def threaded_map(function: Any, values: list[Any], max_workers: int) -> list[Any]:
    """Run blocking shared-filesystem work without blocking the event loop."""
    if not values:
        return []
    with ThreadPoolExecutor(
        max_workers=min(max_workers, len(values)),
        thread_name_prefix="query-resume-io",
    ) as executor:
        return list(executor.map(function, values))


async def case_io(
    args: argparse.Namespace,
    function: Any,
    *values: Any,
    **keywords: Any,
) -> Any:
    """Run one blocking per-case filesystem operation on the I/O pool."""
    executor = getattr(args, "case_io_executor", None)
    return await asyncio.get_running_loop().run_in_executor(
        executor, partial(function, *values, **keywords)
    )


async def read_json_io(args: argparse.Namespace, path: Path) -> Any:
    return await case_io(args, common.read_json, path)


async def write_json_io(args: argparse.Namespace, path: Path, value: Any) -> None:
    await case_io(args, common.write_json, path, value)


def reset_json_directory(path: Path, remove_directory: bool = False) -> None:
    if not path.is_dir():
        return
    for old in path.glob("*.json"):
        old.unlink()
    if remove_directory:
        path.rmdir()


def hierarchy_graph_and_skills(
    hierarchy_categories: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, str]]:
    nodes: dict[str, dict[str, Any]] = {}
    skills: dict[str, dict[str, Any]] = {}
    category_ids: dict[str, str] = {}
    roots = []
    for category in hierarchy_categories:
        name = nonempty(category.get("name"), "Category name")
        category_ids[name] = nonempty(category.get("category_id"), "Category id")
        parent_names = list(category.get("parent_names", []))
        if not parent_names:
            roots.append(name)
        nodes[name] = {
            "name": name,
            "description": str(category.get("description", "")),
            "parent_names": parent_names,
            "child_names": list(category.get("child_names", [])),
            "direct_skill_names": list(category.get("direct_skill_names", [])),
        }
        for skill in category.get("skills", []):
            skill_name = nonempty(skill.get("name"), "Skill name")
            if skill_name in skills and skills[skill_name] != skill:
                raise ValueError(f"conflicting complete documents for Skill {skill_name}")
            skills[skill_name] = skill
    return {"nodes": nodes, "root_node_names": roots}, skills, category_ids


async def process_one(
    position: int,
    source_row: dict[str, Any],
    hierarchy_categories: list[dict[str, Any]],
    output_root: Path,
    api_semaphore: asyncio.Semaphore,
    expansion_semaphore: asyncio.Semaphore,
    consistency_semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> dict[str, Any]:
    query = str(source_row["query"]).strip()
    record = output_root / "records" / f"{position:04d}"
    workspace = record / "workspace"
    result_path = record / "result.json"
    graph, skill_by_name, category_ids = hierarchy_graph_and_skills(hierarchy_categories)
    if args.resume and await case_io(args, result_path.is_file):
        result = await read_json_io(args, result_path)
        if result.get("data_id") == source_row["data_id"]:
            return await finalize_result(result, source_row, record, consistency_semaphore, args)

    await case_io(args, workspace.mkdir, parents=True, exist_ok=True)
    await write_json_io(args, workspace / "original_query.json", {"query": query})
    started = time.time()

    async def request_json(prompt: str, validator: Any, phase: str):
        async with api_semaphore:
            return await direct_api.retry_direct_dialogue(
                prompt,
                phase,
                f"query-{position:04d}",
                validator,
                args,
            )

    retrieval = await retrieve_skills(
        query,
        graph,
        skill_by_name,
        request_json,
        review_batch_size=args.review_batch_size,
        top_k=args.top_k,
    )
    candidate_names = retrieval["candidate_skill_names"]
    selected_names = [item["name"] for item in retrieval["ranking"]]
    selected_skills = [skill_by_name[name] for name in selected_names]
    selected_categories = [
        {"name": name, "description": graph["nodes"][name].get("description", "")}
        for name in retrieval["selected_node_names"]
    ]
    await asyncio.gather(
        write_json_io(args, record / "dag_retrieval.json", retrieval),
        write_json_io(args, workspace / "selected_categories.json", {"categories": selected_categories}),
        write_json_io(args, workspace / "candidate_skills.json", {
            "skills": [skill_by_name[name] for name in candidate_names]
        }),
        write_json_io(args, workspace / "selected_skills.json", {"skills": selected_skills}),
    )

    expansion, expansion_trajectory = await expand_via_direct_api(
        position,
        query,
        selected_skills,
        retrieval["decisions"],
        record,
        expansion_semaphore,
        args,
    )
    await write_json_io(args, workspace / "expanded_query.json", expansion)
    category_selection = {
        "ranking": [{
            "category_name": item["name"],
            "reason": item["reason"],
            "confidence": item["confidence"],
        } for trace in retrieval["routing_traces"] for item in trace["ranking"]],
        "unmatched_goal_needs": [],
    }
    result = {
        "query_index": position,
        "data_id": source_row["data_id"],
        "original_query": query,
        "skill_expanded_query": expansion["query"],
        "expanded_query": expansion["query"],
        "category_selection": category_selection,
        "selected_category_ids": [category_ids[name] for name in retrieval["selected_node_names"]],
        "selected_node_names": retrieval["selected_node_names"],
        "candidate_skill_count": len(candidate_names),
        "candidate_skill_names": candidate_names,
        "skill_decisions": expansion["skill_decisions"],
        "selected_skill_names": expansion["selected_skill_names"],
        "rejected_skill_names": [
            item["name"] for item in expansion["skill_decisions"]
            if item["decision"] == "reject"
        ],
        "retrieval_ranking": retrieval["ranking"],
        "routing_traces": retrieval["routing_traces"],
        "review_traces": retrieval["review_traces"],
        "expanded_row": {**source_row, "query": expansion["query"]},
        "duration_seconds": round(time.time() - started, 3),
    }
    await asyncio.gather(
        write_json_io(args, record / "final_skill_selection.json", {
            "skill_decisions": expansion["skill_decisions"],
            "selected_skill_names": expansion["selected_skill_names"],
        }),
        write_json_io(args, record / "expansion_trajectory.json", {
            "retrieval": {
                "routing": retrieval["routing_traces"],
                "review": retrieval["review_traces"],
            },
            "query_expansion": expansion_trajectory,
        }),
    )
    return await finalize_result(result, source_row, record, consistency_semaphore, args)


async def run(args: argparse.Namespace) -> int:
    if args.case_concurrency <= 0:
        raise ValueError("case_concurrency must be positive")
    if args.model_concurrency <= 0:
        raise ValueError("model_concurrency must be positive")
    if args.model_concurrency_per_model <= 0:
        raise ValueError("model_concurrency_per_model must be positive")
    if args.resume_io_concurrency <= 0:
        raise ValueError("resume_io_concurrency must be positive")
    if args.transport_retries < 0:
        raise ValueError("transport_retries must be nonnegative")
    if args.max_global_backoff <= 0:
        raise ValueError("max_global_backoff must be positive")
    if args.model_router_min_samples <= 0:
        raise ValueError("model_router_min_samples must be positive")
    if args.model_router_slow_ratio <= 1:
        raise ValueError("model_router_slow_ratio must be greater than 1")
    if args.model_router_switch_after_failures <= 0:
        raise ValueError("model_router_switch_after_failures must be positive")
    if args.model_router_error_window <= 0:
        raise ValueError("model_router_error_window must be positive")
    if not 0 < args.model_router_error_rate <= 1:
        raise ValueError("model_router_error_rate must be in (0, 1]")
    if not 0 < args.batch_tail_fraction <= 1:
        raise ValueError("batch_tail_fraction must be in (0, 1]")
    if args.batch_tail_stall_seconds <= 0:
        raise ValueError("batch_tail_stall_seconds must be positive")
    input_path = Path(args.input_jsonl).resolve()
    hierarchy_root = Path(args.hierarchy_dir).resolve()
    output_root = Path(args.output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    args.model_pool = direct_api.parse_model_pool(args.model_pool, args.model)
    all_rows = read_rows(input_path)
    indexed = list(enumerate(all_rows, 1))[args.start:args.end]
    if args.limit > 0:
        indexed = indexed[:args.limit]
    hierarchy = common.read_json(hierarchy_root / "category_skill_index.json")
    hierarchy_categories = hierarchy["categories"]
    category_index = [
        {key: item[key] for key in ("name", "description")}
        for item in hierarchy_categories
    ]
    common.write_json(output_root / "run_config.json", {
        **vars(args), "input_jsonl": str(input_path), "hierarchy_dir": str(hierarchy_root),
        "output_dir": str(output_root), "api_key": "<redacted>" if args.api_key else "",
    })
    common.write_json(output_root / "category_index.json", {"categories": category_index})
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
        max_workers=args.model_concurrency,
        thread_name_prefix="query-model-http",
    ))
    args.case_io_executor = ThreadPoolExecutor(
        max_workers=args.resume_io_concurrency,
        thread_name_prefix="query-case-io",
    )
    args.request_gate = direct_api.AdaptiveRequestGate(
        args.model_concurrency,
        args.max_global_backoff,
    )
    args.model_router = direct_api.AdaptiveModelRouter(
        args.model_pool,
        min_samples=args.model_router_min_samples,
        slow_ratio=args.model_router_slow_ratio,
        error_cooldown=args.model_router_error_cooldown,
        slow_cooldown=args.model_router_slow_cooldown,
        quota_cooldown=args.model_router_quota_cooldown,
        max_cooldown=args.max_global_backoff,
        probe_interval=args.model_router_probe_interval,
        switch_after_failures=args.model_router_switch_after_failures,
        error_window=args.model_router_error_window,
        error_min_samples=args.model_router_error_min_samples,
        error_rate_threshold=args.model_router_error_rate,
        disable_circuit=args.model_router_disable_circuit,
        per_model_concurrency=args.model_concurrency_per_model,
        state_path=output_root / "model_router_state.json",
    )
    print(
        f"MODEL_REQUEST_GATE concurrency={args.model_concurrency} "
        f"per_model={args.model_concurrency_per_model} "
        f"transport_retries={args.transport_retries} "
        f"max_global_backoff={args.max_global_backoff:g}s",
        flush=True,
    )
    print(
        f"MODEL_ROUTER pool={','.join(args.model_pool)} "
        f"min_samples={args.model_router_min_samples} "
        f"slow_ratio={args.model_router_slow_ratio:g} "
        f"error_cooldown={args.model_router_error_cooldown:g}s "
        f"slow_cooldown={args.model_router_slow_cooldown:g}s "
        f"quota_cooldown={args.model_router_quota_cooldown:g}s "
        f"probe_interval={args.model_router_probe_interval:g}s "
        f"switch_after={args.model_router_switch_after_failures} "
        f"error_window={args.model_router_error_window} "
        f"error_min_samples={args.model_router_error_min_samples} "
        f"error_rate={args.model_router_error_rate:.0%} "
        f"disable_circuit={int(args.model_router_disable_circuit)}",
        flush=True,
    )
    def inspect_resume_state(item: tuple[int, dict[str, Any]]) -> tuple[int, str]:
        position, row = item
        record = output_root / "records" / f"{position:04d}"
        result_path = record / "result.json"
        has_expansion = False
        if result_path.is_file():
            try:
                saved_result = common.read_json(result_path)
                has_expansion = saved_result.get("data_id") == row["data_id"]
            except Exception:
                has_expansion = False
        has_consistency = has_expansion and (record / "consistency_review.json").is_file()
        if args.resume and has_expansion and has_consistency:
            state = "fully_cached"
        elif args.resume and has_expansion:
            state = "consistency_only"
        else:
            state = "new"
        return position, state

    resume_scan_started = time.time()
    resume_pairs = await asyncio.to_thread(
        threaded_map,
        inspect_resume_state,
        indexed,
        args.resume_io_concurrency,
    )
    resume_state = dict(resume_pairs)
    state_counts = {
        state: sum(value == state for value in resume_state.values())
        for state in ("fully_cached", "consistency_only", "new")
    }
    print(
        "RESUME_SCAN "
        + " ".join(f"{key}={value}" for key, value in state_counts.items())
        + f" total={len(indexed)} resume={args.resume} "
        + f"io_workers={args.resume_io_concurrency} "
        + f"elapsed={time.time() - resume_scan_started:.2f}s",
        flush=True,
    )
    for position, row in indexed:
        if resume_state[position] != "new":
            print(
                f"[resume-plan] query-{position:04d} data_id={row['data_id']} "
                f"mode={resume_state[position]}",
                flush=True,
            )
    api_semaphore = asyncio.Semaphore(args.category_concurrency)
    expansion_semaphore = asyncio.Semaphore(args.expansion_concurrency)
    consistency_semaphore = asyncio.Semaphore(args.consistency_concurrency)
    fully_cached = [
        (position, row) for position, row in indexed
        if resume_state[position] == "fully_cached"
    ]

    def load_fully_cached(item: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        position, row = item
        result = common.read_json(output_root / "records" / f"{position:04d}" / "result.json")
        if result.get("data_id") != row["data_id"]:
            raise ValueError(f"fully cached query-{position:04d} changed data_id during resume")
        return position, result

    cached_load_started = time.time()
    cached_pairs = await asyncio.to_thread(
        threaded_map,
        load_fully_cached,
        fully_cached,
        args.resume_io_concurrency,
    )
    completed: dict[int, dict[str, Any]] = dict(cached_pairs)
    print(
        f"RESUME_LOAD fully_cached={len(completed)} "
        f"io_workers={args.resume_io_concurrency} "
        f"elapsed={time.time() - cached_load_started:.2f}s",
        flush=True,
    )
    published = await case_io(args, materialize_incremental_jsonl, output_root, completed)
    print(
        f"[incremental-jsonl] published={published}/{len(indexed)} "
        f"path={output_root / 'expanded_queries.jsonl'}",
        flush=True,
    )
    runnable = [
        (position, row) for position, row in indexed
        if resume_state[position] != "fully_cached"
    ]
    progress = tqdm(total=len(runnable), desc="DAG retrieval -> Query expansion", unit="query")
    tail_fused = False
    deferred_positions: list[int] = []
    case_concurrency = min(args.case_concurrency, len(runnable)) if runnable else 0
    tail_limit = max(1, int(case_concurrency * args.batch_tail_fraction)) if case_concurrency else 0
    direct_api.timestamped_write(
        f"[case-pool] total={len(runnable)} max_in_flight={case_concurrency} "
        f"per_case_stages=serial tail_limit={tail_limit} "
        f"tail_stall_seconds={args.batch_tail_stall_seconds:g}"
    )
    next_runnable = 0
    pending: dict[asyncio.Task, tuple[int, dict[str, Any]]] = {}
    unpublished_completions = 0
    last_incremental_publish = time.monotonic()

    def fill_case_pool() -> None:
        nonlocal next_runnable
        while len(pending) < case_concurrency and next_runnable < len(runnable):
            position, row = runnable[next_runnable]
            next_runnable += 1
            task = asyncio.create_task(process_one(
                position, row, hierarchy_categories, output_root,
                api_semaphore, expansion_semaphore, consistency_semaphore, args,
            ))
            pending[task] = (position, row)

    fill_case_pool()
    while pending:
        input_exhausted = next_runnable >= len(runnable)
        timeout = (
            args.batch_tail_stall_seconds
            if input_exhausted and len(pending) <= tail_limit
            else None
        )
        done, _ = await asyncio.wait(
            set(pending),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            deferred_positions = sorted(pending[task][0] for task in pending)
            direct_api.timestamped_write(
                f"[case-tail-fuse] completed={len(runnable) - len(pending)}/{len(runnable)} "
                f"remaining={len(pending)} <= tail_limit={tail_limit}; "
                f"no completed case for {args.batch_tail_stall_seconds:g}s; "
                "cancelling tail and requesting a fresh-session restart"
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await write_json_io(args, output_root / "deferred_tail.json", {
                "reason": "case_tail_stall",
                "max_in_flight_cases": case_concurrency,
                "tail_limit": tail_limit,
                "stall_seconds": args.batch_tail_stall_seconds,
                "query_indices": deferred_positions,
                "data_ids": [pending[task][1]["data_id"] for task in pending],
            })
            tail_fused = True
            break
        for task in done:
            pending.pop(task)
            result = task.result()
            completed[result["query_index"]] = result
            position = result["query_index"]
            direct_api.timestamped_write(
                f"[{resume_state[position]}] query-{position:04d} "
                f"data_id={result['data_id']} complete; buffered_for_jsonl"
            )
            progress.update(1)
            unpublished_completions += 1
        fill_case_pool()
        publish_due = (
            unpublished_completions >= 50
            or time.monotonic() - last_incremental_publish >= 10.0
            or not pending
        )
        if unpublished_completions and publish_due:
            published = await case_io(
                args, materialize_incremental_jsonl, output_root, completed
            )
            direct_api.timestamped_write(
                f"[incremental-jsonl] published={published}/{len(indexed)} "
                f"newly_completed={unpublished_completions}"
            )
            unpublished_completions = 0
            last_incremental_publish = time.monotonic()
    progress.close()
    await case_io(args, materialize_incremental_jsonl, output_root, completed)
    incomplete = [position for position, _ in indexed if position not in completed]
    if incomplete:
        partial_summary = {
            "status": "restart_requested" if tail_fused else "incomplete",
            "expanded_queries": len(completed),
            "source_queries": len(indexed),
            "incomplete_queries": len(incomplete),
            "deferred_tail_queries": deferred_positions,
            "output_jsonl": str(output_root / "expanded_queries.jsonl"),
        }
        await write_json_io(args, output_root / "partial_summary.json", partial_summary)
        print(json.dumps(partial_summary, ensure_ascii=False, indent=2), flush=True)
        return 75

    ordered = [completed[position] for position, _ in indexed]
    for stale_path in (output_root / "deferred_tail.json", output_root / "partial_summary.json"):
        if stale_path.exists():
            stale_path.unlink()
    summary = {
        "expanded_queries": len(ordered),
        "selected_category_count_distribution": {
            str(count): sum(len(item["selected_category_ids"]) == count for item in ordered)
            for count in sorted({len(item["selected_category_ids"]) for item in ordered})
        },
        "average_candidate_skills": (
            sum(item["candidate_skill_count"] for item in ordered) / len(ordered) if ordered else 0
        ),
        "intent_consistency_changed": sum(item["consistency_changed"] for item in ordered),
        "intent_consistency_passed_unchanged": sum(not item["consistency_changed"] for item in ordered),
        "reviewed_skills": sum(len(item["skill_decisions"]) for item in ordered),
        "accepted_skills": sum(len(item["selected_skill_names"]) for item in ordered),
        "rejected_after_full_read": sum(len(item["rejected_skill_names"]) for item in ordered),
        "queries_with_post_read_rejection": sum(bool(item["rejected_skill_names"]) for item in ordered),
        "output_jsonl": str(output_root / "expanded_queries.jsonl"),
    }
    await write_json_io(args, output_root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Shared recursive DAG routing -> full-document review -> Query expansion"
    )
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--hierarchy-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start", type=int, default=int(os.environ.get("START", "0")))
    parser.add_argument("--end", type=int, default=int(os.environ["END"]) if os.environ.get("END") else None)
    parser.add_argument("--limit", type=int, default=int(os.environ.get("LIMIT", "10")))
    parser.add_argument("--category-concurrency", type=int, default=int(os.environ.get("CATEGORY_CONCURRENCY", "10")))
    parser.add_argument("--expansion-concurrency", type=int, default=int(os.environ.get("QUERY_EXPANSION_CONCURRENCY", "10")))
    parser.add_argument("--consistency-concurrency", type=int, default=int(os.environ.get("CONSISTENCY_CONCURRENCY", "10")))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--review-batch-size", type=int, default=8)
    parser.add_argument(
        "--case-concurrency",
        type=int,
        default=int(os.environ.get("CASE_CONCURRENCY", "100")),
        help="Maximum number of complete per-Query pipelines in flight at once.",
    )
    parser.add_argument(
        "--resume-io-concurrency",
        type=int,
        default=int(os.environ.get("RESUME_IO_CONCURRENCY", "256")),
        help="Dedicated thread count for resume scans and cached-result loading.",
    )
    parser.add_argument(
        "--model-concurrency",
        type=int,
        default=int(os.environ.get("MODEL_REQUEST_CONCURRENCY", "32")),
        help="Global limit for simultaneous model HTTP requests across all stages.",
    )
    parser.add_argument(
        "--model-concurrency-per-model",
        type=int,
        default=int(os.environ.get("MODEL_REQUEST_CONCURRENCY_PER_MODEL", "1000")),
        help="Hard simultaneous-request limit for each model route across all stages.",
    )
    parser.add_argument(
        "--transport-retries",
        type=int,
        default=int(os.environ.get("MODEL_TRANSPORT_RETRIES", "5")),
        help="Connection/read/5xx retries inside each HTTP attempt.",
    )
    parser.add_argument(
        "--transport-backoff-factor",
        type=float,
        default=float(os.environ.get("MODEL_TRANSPORT_BACKOFF_FACTOR", "0.5")),
    )
    parser.add_argument(
        "--max-global-backoff",
        type=float,
        default=float(os.environ.get("MODEL_MAX_GLOBAL_BACKOFF", "120")),
        help="Maximum shared cooldown after 429/5xx/transport failures.",
    )
    parser.add_argument(
        "--batch-tail-fraction",
        type=float,
        default=float(os.environ.get("QUERY_BATCH_TAIL_FRACTION", "0.05")),
        help="Enable the stall watchdog only for this final fraction of each task batch.",
    )
    parser.add_argument(
        "--batch-tail-stall-seconds",
        type=float,
        default=float(os.environ.get("QUERY_BATCH_TAIL_STALL_SECONDS", "7200")),
        help="Restart with fresh sessions when the watched batch tail stops completing queries.",
    )
    parser.add_argument("--api-timeout", type=int, default=int(os.environ.get("EXTERNAL_API_TIMEOUT", "300")))
    parser.add_argument("--max-output-tokens", type=int, default=int(os.environ.get("EXTERNAL_MAX_OUTPUT_TOKENS", "8192")))
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("SKILL_API_KEY", os.environ.get("API_KEY", "")))
    parser.add_argument("--model", default=os.environ.get("SKILL_MODEL_NAME", os.environ.get("MODEL", "")))
    parser.add_argument(
        "--model-pool",
        default=os.environ.get("SKILL_MODEL_POOL", ""),
        help="Comma/space-separated interchangeable model routes used by the adaptive router.",
    )
    parser.add_argument(
        "--model-router-min-samples",
        type=int,
        default=int(os.environ.get("MODEL_ROUTER_MIN_SAMPLES", "8")),
    )
    parser.add_argument(
        "--model-router-slow-ratio",
        type=float,
        default=float(os.environ.get("MODEL_ROUTER_SLOW_RATIO", "1.8")),
    )
    parser.add_argument(
        "--model-router-error-cooldown",
        type=float,
        default=float(os.environ.get("MODEL_ROUTER_ERROR_COOLDOWN", "15")),
    )
    parser.add_argument(
        "--model-router-slow-cooldown",
        type=float,
        default=float(os.environ.get("MODEL_ROUTER_SLOW_COOLDOWN", "60")),
    )
    parser.add_argument(
        "--model-router-quota-cooldown",
        type=float,
        default=float(os.environ.get("MODEL_ROUTER_QUOTA_COOLDOWN", "300")),
    )
    parser.add_argument(
        "--model-router-probe-interval",
        type=float,
        default=float(os.environ.get("MODEL_ROUTER_PROBE_INTERVAL", "120")),
    )
    parser.add_argument(
        "--model-router-switch-after-failures",
        type=int,
        default=int(os.environ.get("MODEL_ROUTER_SWITCH_AFTER_FAILURES", "2")),
    )
    parser.add_argument(
        "--model-router-error-window",
        type=int,
        default=int(os.environ.get("MODEL_ROUTER_ERROR_WINDOW", "20")),
    )
    parser.add_argument(
        "--model-router-error-min-samples",
        type=int,
        default=int(os.environ.get("MODEL_ROUTER_ERROR_MIN_SAMPLES", "20")),
    )
    parser.add_argument(
        "--model-router-error-rate",
        type=float,
        default=float(os.environ.get("MODEL_ROUTER_ERROR_RATE", "0.30")),
    )
    parser.add_argument(
        "--model-router-disable-circuit",
        action=argparse.BooleanOptionalAction,
        default=common.env_bool("MODEL_ROUTER_DISABLE_CIRCUIT", False),
    )
    parser.add_argument("--retries", type=int, default=int(os.environ.get("SKILL_RETRIES", "2")))
    parser.add_argument("--retry-forever", action=argparse.BooleanOptionalAction, default=common.env_bool("SKILL_RETRY_FOREVER", True))
    parser.add_argument("--retry-delay", type=float, default=float(os.environ.get("SKILL_RETRY_DELAY", "30")))
    parser.add_argument("--print-messages", action=argparse.BooleanOptionalAction, default=common.env_bool("SKILL_PRINT_MESSAGES", False))
    parser.add_argument("--message-preview-chars", type=int, default=int(os.environ.get("SKILL_MESSAGE_PREVIEW_CHARS", "240")))
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=common.env_bool("RESUME", True))
    return parser


def main() -> int:
    args = parser().parse_args()
    if not args.model:
        raise ValueError("--model, SKILL_MODEL_NAME, or MODEL is required")
    if not args.api_key:
        raise ValueError("SKILL_API_KEY or API_KEY is required")
    try:
        # asyncio.run shuts down the default model-HTTP executor before this
        # finally block runs, so no worker can still be using the shared pool.
        return asyncio.run(run(args))
    finally:
        case_io_executor = getattr(args, "case_io_executor", None)
        if case_io_executor is not None:
            case_io_executor.shutdown(wait=True, cancel_futures=True)
        direct_api.close_shared_http_adapters()


if __name__ == "__main__":
    raise SystemExit(main())
