#!/usr/bin/env python3
"""Incrementally distill every available G/E/I case into a directory-backed cache."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from tqdm import tqdm

import common_model_api as direct_api
import observable_behavior_contract as hierarchy
import common_io as common


QUERY_LEVELS = ["goal", "experience", "interaction"]


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


def record_case_id(directory: Path) -> str:
    result_path = directory / "result.json"
    if result_path.is_file():
        try:
            value = common.read_json(result_path)
            case_id = str(value.get("case_id", "")).strip()
            if case_id:
                return case_id
        except Exception:
            pass
    name = directory.name
    if name.startswith("codrops-"):
        return name
    if "_" in name:
        return name.split("_", 1)[1]
    return name


def stored_input(directory: Path) -> dict[str, str] | None:
    for path in (directory / "input.json", directory / "workspace" / "case.json"):
        if not path.is_file():
            continue
        try:
            value = common.read_json(path)
            if all(str(value.get(key, "")).strip() for key in ("goal", "experience", "interaction")):
                return {key: str(value[key]).strip() for key in ("goal", "experience", "interaction")}
        except Exception:
            pass
    return None


def scan_completed(root: Path) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    records_dir = root / "distillation" / "records"
    if not records_dir.is_dir():
        return completed
    for final_path in sorted(records_dir.glob("*/distillation.json")):
        directory = final_path.parent
        try:
            value = hierarchy.validate_distillation(common.read_json(final_path))
        except Exception:
            continue
        case_id = record_case_id(directory)
        if case_id in completed:
            raise ValueError(f"duplicate valid distillation records for {case_id}")
        completed[case_id] = {
            "directory": directory,
            "distillation": value,
            "input": stored_input(directory),
        }
    return completed


def load_unique_cases(path: Path) -> tuple[list[dict[str, str]], int]:
    """Load the full dataset, collapsing only byte-equivalent duplicate website IDs."""
    cases: list[dict[str, str]] = []
    by_id: dict[str, dict[str, str]] = {}
    duplicate_lines = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = str(row.get("website_id", "")).strip()
            segments = row.get("segments")
            if not case_id:
                raise ValueError(f"missing website_id at line {line_number}")
            if not isinstance(segments, list) or [item.get("level") for item in segments] != QUERY_LEVELS:
                raise ValueError(f"invalid segment levels for {case_id} at line {line_number}")
            texts = {item["level"]: str(item.get("text", "")).strip() for item in segments}
            if not all(texts[level] for level in ("goal", "experience", "interaction")):
                raise ValueError(f"empty G/E/I for {case_id} at line {line_number}")
            case = {
                "case_id": case_id,
                "goal": texts["goal"],
                "experience": texts["experience"],
                "interaction": texts["interaction"],
            }
            previous = by_id.get(case_id)
            if previous is not None:
                if previous != case:
                    raise ValueError(f"conflicting duplicate website_id {case_id} at line {line_number}")
                duplicate_lines += 1
                continue
            by_id[case_id] = case
            cases.append(case)
    return cases, duplicate_lines


def load_excluded_case_ids(path: str) -> set[str]:
    if not path:
        return set()
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Excluded case ID file does not exist: {source}")
    values = {
        line.strip()
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    return values


async def messages_request_until_200(
    prompt: str,
    case_id: str,
    session_id: str,
    trajectory: dict[str, Any],
    trajectory_path: Path,
    semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request_attempt = 0
    while True:
        request_attempt += 1
        started = time.time()
        try:
            async with semaphore:
                request_payload, raw_response = await asyncio.to_thread(
                    direct_api.call_messages_api, prompt, args, session_id
                )
            return request_payload, raw_response
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            trajectory["attempts"].append({
                "request_attempt": request_attempt,
                "status": "request_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "duration_seconds": round(time.time() - started, 3),
            })
            common.write_json(trajectory_path, trajectory)
            tqdm.write(
                f"[distillation-request-retry] {case_id} request_attempt={request_attempt} "
                f"error={type(exc).__name__}: {exc}; retrying in {args.request_retry_delay}s"
            )
            await asyncio.sleep(args.request_retry_delay)


async def generate_one(
    case: dict[str, str],
    directory: Path,
    semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    input_value = {key: case[key] for key in ("goal", "experience", "interaction")}
    common.write_json(directory / "input.json", input_value)
    prompt_base = hierarchy.api_prompt(hierarchy.DISTILL_PROMPT) + "\n\nInput:\n" + json.dumps(
        input_value, ensure_ascii=False, indent=2
    )
    trajectory_path = directory / "trajectory.json"
    trajectory: dict[str, Any] = {
        "case_id": case["case_id"],
        "phase": "category_distill",
        "session_id": str(uuid.uuid4()),
        "attempts": [],
    }
    validation_error = ""
    generation_attempt = 0
    while True:
        generation_attempt += 1
        prompt = prompt_base
        if validation_error:
            prompt += (
                "\n\nThe previous response was rejected by the output validator: "
                + validation_error
                + "\nReturn a corrected JSON object only."
            )
        started = time.time()
        request_payload, raw_response = await messages_request_until_200(
            prompt,
            case["case_id"],
            trajectory["session_id"],
            trajectory,
            trajectory_path,
            semaphore,
            args,
        )
        text = direct_api.response_text(raw_response)
        try:
            value = hierarchy.validate_distillation(direct_api.extract_json_object(text))
        except Exception as exc:
            validation_error = str(exc)
            trajectory["attempts"].append({
                "generation_attempt": generation_attempt,
                "status": "validation_failed",
                "error": validation_error,
                "duration_seconds": round(time.time() - started, 3),
                "messages": request_payload["messages"] + [{"role": "assistant", "content": text}],
                "response": raw_response,
            })
            common.write_json(trajectory_path, trajectory)
            if not args.validation_retry_forever and generation_attempt > args.validation_retries:
                raise
            tqdm.write(
                f"[distillation-validation-retry] {case['case_id']} "
                f"attempt={generation_attempt} error={validation_error}"
            )
            continue
        trajectory["attempts"].append({
            "generation_attempt": generation_attempt,
            "status": "accepted",
            "duration_seconds": round(time.time() - started, 3),
            "messages": request_payload["messages"] + [{"role": "assistant", "content": text}],
            "response": raw_response,
        })
        common.write_json(trajectory_path, trajectory)
        common.write_json(directory / "distillation.json", value)
        common.write_json(directory / "result.json", {
            "case_id": case["case_id"],
            "status": "success",
            "category_candidates": len(value["category_candidates"]),
            "skill_candidates": len(value["skill_candidates"]),
        })
        return {"case_id": case["case_id"], "goal": case["goal"], **value}


async def process_one(
    case: dict[str, str], root: Path, semaphore: asyncio.Semaphore, args: argparse.Namespace
) -> dict[str, Any]:
    directory = root / "distillation" / "records" / case["case_id"]
    try:
        row = await generate_one(case, directory, semaphore, args)
        return {"case_id": case["case_id"], "status": "success", "row": row, "error": ""}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        common.write_json(directory / "result.json", {
            "case_id": case["case_id"],
            "status": "failed",
            "error": str(exc),
        })
        return {"case_id": case["case_id"], "status": "failed", "row": None, "error": str(exc)}


async def run(args: argparse.Namespace) -> int:
    query_path = Path(args.queries_jsonl).resolve()
    root = Path(args.output_dir).resolve()
    if not query_path.is_file():
        raise FileNotFoundError(f"Query dataset does not exist: {query_path}")
    root.mkdir(parents=True, exist_ok=True)
    input_cases, duplicate_input_lines = load_unique_cases(query_path)
    excluded_ids = load_excluded_case_ids(args.exclude_case_ids_file)
    input_case_ids = {case["case_id"] for case in input_cases}
    unknown_excluded_ids = sorted(excluded_ids - input_case_ids)
    cases = [case for case in input_cases if case["case_id"] not in excluded_ids]
    excluded_cases = [case for case in input_cases if case["case_id"] in excluded_ids]
    if not cases:
        raise ValueError("Query dataset contains no cases")
    completed = scan_completed(root) if args.resume else {}
    selected_ids = {case["case_id"] for case in cases}
    completed = {key: value for key, value in completed.items() if key in selected_ids}
    pending = [case for case in cases if case["case_id"] not in completed]
    input_changes = sum(
        1
        for case in cases
        if case["case_id"] in completed
        and completed[case["case_id"]]["input"] is not None
        and completed[case["case_id"]]["input"] != {key: case[key] for key in ("goal", "experience", "interaction")}
    )
    print(
        f"[distillation-resume-scan] input={len(input_cases)} excluded={len(excluded_cases)} "
        f"selected={len(cases)} completed={len(completed)} "
        f"pending={len(pending)} identical_duplicate_input_lines={duplicate_input_lines} "
        f"preserved_completed_input_changes={input_changes} "
        f"unknown_excluded_ids={len(unknown_excluded_ids)}",
        flush=True,
    )
    common.write_json(root / "distillation" / "run_config.json", {
        **vars(args),
        "queries_jsonl": str(query_path),
        "api_key": "<redacted>" if args.api_key else "",
    })
    (root / "distillation" / "selected_ids.txt").write_text(
        "".join(case["case_id"] + "\n" for case in cases), encoding="utf-8"
    )
    (root / "distillation" / "excluded_ids.txt").write_text(
        "".join(case["case_id"] + "\n" for case in excluded_cases), encoding="utf-8"
    )

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [asyncio.create_task(process_one(case, root, semaphore, args)) for case in pending]
    generated: dict[str, dict[str, Any]] = {}
    manifest: list[dict[str, Any]] = []
    progress = tqdm(total=len(tasks), desc="distillation pending", unit="case")
    for task in asyncio.as_completed(tasks):
        result = await task
        manifest.append({key: result[key] for key in ("case_id", "status", "error")})
        if result["row"] is not None:
            generated[result["case_id"]] = result["row"]
        progress.update(1)
        progress.set_postfix(failed=sum(item["status"] == "failed" for item in manifest))
    progress.close()

    rows: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["case_id"]
        if case_id in generated:
            rows.append(generated[case_id])
            continue
        cached = completed.get(case_id)
        if cached is None:
            continue
        cached_input = cached["input"]
        goal = cached_input["goal"] if cached_input is not None else case["goal"]
        rows.append({"case_id": case_id, "goal": goal, **cached["distillation"]})
        manifest.append({"case_id": case_id, "status": "skipped_existing", "error": ""})
    order = {case["case_id"]: index for index, case in enumerate(cases)}
    manifest.sort(key=lambda item: order[item["case_id"]])
    common.write_jsonl(root / "distillation" / "manifest.jsonl", manifest)
    common.write_jsonl(root / "distillation" / "distillations.jsonl", rows)
    failed = [item for item in manifest if item["status"] == "failed"]
    summary = {
        "input_cases": len(input_cases),
        "excluded_cases": len(excluded_cases),
        "selected_cases": len(cases),
        "reused_completed": len(completed),
        "newly_completed": len(generated),
        "failed": len(failed),
        "cache_rows": len(rows),
        "preserved_completed_input_changes": input_changes,
        "identical_duplicate_input_lines": duplicate_input_lines,
        "output_cache": str(root / "distillation" / "distillations.jsonl"),
    }
    common.write_json(root / "distillation" / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Resume a directory-backed G/E/I distillation run and generate only missing cases."
    )
    value.add_argument("--queries-jsonl", required=True)
    value.add_argument("--output-dir", required=True)
    value.add_argument(
        "--exclude-case-ids-file",
        default=os.environ.get("DISTILL_EXCLUDE_CASE_IDS_FILE", ""),
        help="Optional newline-delimited case IDs to omit deterministically before Distillation.",
    )
    value.add_argument("--concurrency", type=int, default=int(os.environ.get("DISTILL_CONCURRENCY", "50")))
    value.add_argument("--resume", action=argparse.BooleanOptionalAction, default=env_bool("RESUME", True))
    value.add_argument("--model", default=os.environ.get("SKILL_MODEL_NAME", os.environ.get("MODEL", "")))
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("SKILL_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--api-timeout", type=int, default=int(os.environ.get("CATEGORY_API_TIMEOUT", "300")))
    value.add_argument("--max-output-tokens", type=int, default=int(os.environ.get("CATEGORY_MAX_OUTPUT_TOKENS", "16384")))
    value.add_argument("--request-retry-delay", type=float, default=float(os.environ.get("DISTILL_REQUEST_RETRY_DELAY", "5")))
    value.add_argument("--validation-retries", type=int, default=int(os.environ.get("DISTILL_VALIDATION_RETRIES", "2")))
    value.add_argument("--validation-retry-forever", action=argparse.BooleanOptionalAction, default=env_bool("DISTILL_VALIDATION_RETRY_FOREVER", True))
    return value


def main() -> int:
    args = parser().parse_args()
    if not args.model:
        raise ValueError("--model, SKILL_MODEL_NAME, or MODEL is required")
    if not args.api_key:
        raise ValueError("SKILL_API_KEY or API_KEY is required")
    if args.concurrency < 1:
        raise ValueError("DISTILL_CONCURRENCY must be positive")
    if args.validation_retries < 0:
        raise ValueError("DISTILL_VALIDATION_RETRIES must be non-negative")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
