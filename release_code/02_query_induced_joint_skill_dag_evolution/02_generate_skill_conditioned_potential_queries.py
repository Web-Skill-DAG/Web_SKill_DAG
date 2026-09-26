#!/usr/bin/env python3
"""Generate resumable Skill-conditioned Potential Queries."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import uuid
from collections import Counter, deque
from pathlib import Path
from typing import Any

from tqdm import tqdm


from potential_query_transport import (
    ChatTransport,
    TransportConfig,
)

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    GENERATE_POTENTIAL_QUERIES_PROMPT as PROMPT,
)



PROMPT_VERSION = "pqr-potential-queries-v1"
TECHNOLOGY_RE = re.compile(
    r"\b(?:react\.?js|react framework|vue|angular|svelte|three\.?js|gsap|webgl|webgpu|css|javascript|typescript|"
    r"framer[- ]motion|tailwind|shader|glsl|canvas api)\b",
    re.I,
)
CJK_RE = re.compile(r"[\u3400-\u9fff]")
NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
WRITE_LOCK = threading.Lock()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with WRITE_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def load_skills(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_name: dict[str, dict[str, str]] = {}
    categories_by_name: dict[str, list[str]] = {}
    for category in raw["categories"]:
        category_name = str(category["name"])
        for skill in category.get("skills", []):
            name = str(skill["name"]).strip()
            description = str(skill["description"]).strip()
            if name in by_name and by_name[name]["description"] != description:
                raise ValueError(f"conflicting duplicate Skill: {name}")
            by_name[name] = {"name": name, "description": description}
            categories_by_name.setdefault(name, []).append(category_name)
    return [
        {
            "skill_key": f"public-skill-{index:04d}",
            **by_name[name],
            "category_names": sorted(categories_by_name[name]),
        }
        for index, name in enumerate(sorted(by_name), 1)
    ]


def select_skills(skills: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or limit >= len(skills):
        return skills
    by_category: dict[str, deque[dict[str, Any]]] = {}
    for skill in skills:
        primary = skill["category_names"][0] if skill["category_names"] else "uncategorized"
        by_category.setdefault(primary, deque()).append(skill)
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    categories = sorted(by_category)
    while len(selected) < limit:
        progressed = False
        for category in categories:
            queue = by_category[category]
            while queue and queue[0]["skill_key"] in used:
                queue.popleft()
            if queue and len(selected) < limit:
                skill = queue.popleft()
                selected.append(skill)
                used.add(skill["skill_key"])
                progressed = True
        if not progressed:
            break
    if len(selected) != limit:
        raise RuntimeError(f"selected {len(selected)} Skills, expected {limit}")
    return selected


def normalize_query(text: str) -> str:
    return NORMALIZE_RE.sub(" ", text.lower()).strip()


def extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("response contains no JSON object")


def validate_queries(value: dict[str, Any], expected: int) -> list[str]:
    if set(value) != {"potential_queries"}:
        raise ValueError("top-level JSON must contain only potential_queries")
    raw = value["potential_queries"]
    if not isinstance(raw, list) or len(raw) != expected:
        raise ValueError(f"expected exactly {expected} potential_queries, found {len(raw) if isinstance(raw, list) else 'non-list'}")
    queries: list[str] = []
    normalized: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            raise ValueError(f"query {index} is not a string")
        query = " ".join(item.strip().split())
        words = query.split()
        if not query or len(words) < 3 or len(words) > 50:
            raise ValueError(f"query {index} has invalid word count: {len(words)}")
        if CJK_RE.search(query):
            raise ValueError(f"query {index} contains CJK text")
        key = normalize_query(query)
        if not key or key in normalized:
            raise ValueError(f"query {index} is an exact/normalized duplicate")
        normalized.add(key)
        queries.append(query)
    return queries


def valid_result(path: Path, expected: int) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        validate_queries({"potential_queries": value["potential_queries"]}, expected)
        if value.get("prompt_version") != PROMPT_VERSION:
            return None
        return value
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def jaccard(left: str, right: str) -> float:
    a = set(normalize_query(left).split())
    b = set(normalize_query(right).split())
    return len(a & b) / len(a | b) if a or b else 1.0


def quality_audit(results: list[dict[str, Any]]) -> dict[str, Any]:
    all_queries = [query for result in results for query in result["potential_queries"]]
    normalized = [normalize_query(query) for query in all_queries]
    high_similarity_pairs = 0
    compared_pairs = 0
    max_within_skill_jaccard = 0.0
    per_skill: list[dict[str, Any]] = []
    for result in results:
        queries = result["potential_queries"]
        local_high = 0
        local_max = 0.0
        for i in range(len(queries)):
            for j in range(i + 1, len(queries)):
                similarity = jaccard(queries[i], queries[j])
                compared_pairs += 1
                local_max = max(local_max, similarity)
                if similarity >= 0.8:
                    local_high += 1
                    high_similarity_pairs += 1
        max_within_skill_jaccard = max(max_within_skill_jaccard, local_max)
        per_skill.append({
            "skill_key": result["skill_key"],
            "query_count": len(queries),
            "max_pairwise_token_jaccard": round(local_max, 4),
            "pairs_token_jaccard_ge_0_8": local_high,
            "technology_term_hits": sum(bool(TECHNOLOGY_RE.search(query)) for query in queries),
        })
    word_lengths = [len(query.split()) for query in all_queries]
    duplicates = {query: count for query, count in Counter(normalized).items() if count > 1}
    return {
        "skills": len(results),
        "queries": len(all_queries),
        "unique_normalized_queries": len(set(normalized)),
        "cross_skill_normalized_duplicate_groups": len(duplicates),
        "word_length": {
            "min": min(word_lengths),
            "max": max(word_lengths),
            "mean": round(sum(word_lengths) / len(word_lengths), 3),
        },
        "within_skill_pairwise_token_jaccard": {
            "compared_pairs": compared_pairs,
            "pairs_ge_0_8": high_similarity_pairs,
            "rate_ge_0_8": round(high_similarity_pairs / compared_pairs, 6) if compared_pairs else 0.0,
            "maximum": round(max_within_skill_jaccard, 4),
        },
        "technology_term_hits": sum(bool(TECHNOLOGY_RE.search(query)) for query in all_queries),
        "per_skill": per_skill,
    }


def generate_one(skill: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    record_dir = Path(args.output_dir) / "records" / skill["skill_key"]
    result_path = record_dir / "result.json"
    cached = valid_result(result_path, args.queries_per_skill) if args.resume else None
    if cached is not None:
        return cached
    record_dir.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT.format(name=skill["name"], description=skill["description"])
    (record_dir / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PROMPT_VERSION}:{skill['skill_key']}"))
    attempt = 0
    while True:
        attempt += 1
        started = time.time()
        try:
            if args.dry_run:
                queries = [
                    f"Create a web experience variation {index + 1} that supports the intended outcome for this audience."
                    for index in range(args.queries_per_skill)
                ]
                raw_response: dict[str, Any] = {
                    "choices": [{"message": {"role": "assistant", "content": json.dumps({"potential_queries": queries})}}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                }
                metadata = {"http_status": 0, "duration_seconds": 0.0, "session_id": session_id}
            else:
                _, raw_response, metadata = args.transport.request(prompt, args.max_tokens, session_id)
                queries = validate_queries(extract_json_object(extract_text(raw_response)), args.queries_per_skill)
            attempt_dir = record_dir / "attempts"
            atomic_write_json(attempt_dir / f"attempt-{attempt:03d}-response.json", raw_response)
            result = {
                "skill_key": skill["skill_key"],
                "skill_name": skill["name"],
                "skill_description": skill["description"],
                "category_names": skill["category_names"],
                "potential_queries": queries,
                "query_count": len(queries),
                "prompt_version": PROMPT_VERSION,
                "generation_attempts": attempt,
                "usage": raw_response.get("usage", {}),
                "request_metadata": metadata,
            }
            atomic_write_json(result_path, result)
            return result
        except Exception as exc:  # Persist every transport/schema failure before retrying.
            append_jsonl(record_dir / "retries.jsonl", {
                "attempt": attempt,
                "elapsed_seconds": round(time.time() - started, 3),
                "error_type": type(exc).__name__,
                "error": str(exc)[:4000],
            })
            if args.max_attempts > 0 and attempt >= args.max_attempts:
                raise
            time.sleep(args.retry_sleep)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-library", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--queries-per-skill", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--request-concurrency", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=7000)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--request-timeout", type=float, default=1800.0)
    parser.add_argument("--transport-retries", type=int, default=5)
    parser.add_argument("--transport-backoff-factor", type=float, default=0.5)
    parser.add_argument("--max-global-backoff", type=float, default=120.0)
    parser.add_argument("--max-attempts", type=int, default=0, help="0 retries until success")
    parser.add_argument("--retry-sleep", type=float, default=5.0)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.queries_per_skill != 50:
        raise SystemExit("the evolution protocol requires --queries-per-skill 50")
    if not args.dry_run and not args.api_key:
        raise SystemExit("QUERY_API_KEY or --api-key is required")

    skills = load_skills(args.skill_library)
    if len(skills) != 864:
        raise RuntimeError(f"expected current 864-Skill library, found {len(skills)}")
    selected = select_skills(skills, args.limit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "method": "Skill-conditioned Potential Query generation",
        "prompt_version": PROMPT_VERSION,
        "skill_library": str(args.skill_library.resolve()),
        "library_skill_count": len(skills),
        "selected_skill_count": len(selected),
        "queries_per_skill": args.queries_per_skill,
        "expected_queries": len(selected) * args.queries_per_skill,
        "selection": "all" if len(selected) == len(skills) else "category_round_robin",
        "selected_skill_keys": [skill["skill_key"] for skill in selected],
        "construction_constraints": {
            "skill_input": "name + description only",
            "output_language": "English",
        },
        "dry_run": args.dry_run,
    }
    atomic_write_json(args.output_dir / "run_manifest.json", manifest)

    results: list[dict[str, Any]] = []
    transport = None
    if not args.dry_run:
        transport = ChatTransport(TransportConfig(
            base_url=args.base_url,
            api_key=args.api_key,
            connect_timeout=args.connect_timeout,
            read_timeout=args.request_timeout,
            request_concurrency=args.request_concurrency,
            transport_retries=args.transport_retries,
            transport_backoff_factor=args.transport_backoff_factor,
            max_global_backoff=args.max_global_backoff,
        ))
    args.transport = transport
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_to_skill = {executor.submit(generate_one, skill, args): skill for skill in selected}
        for future in tqdm(
            concurrent.futures.as_completed(future_to_skill),
            total=len(future_to_skill),
            desc="Potential Queries",
            unit="skill",
        ):
            results.append(future.result())
    results.sort(key=lambda row: row["skill_key"])
    atomic_write_jsonl(args.output_dir / "potential_queries.jsonl", results)
    atomic_write_json(args.output_dir / "quality_audit.json", quality_audit(results))
    summary = {
        "status": "complete",
        "skills": len(results),
        "queries": sum(len(row["potential_queries"]) for row in results),
        "prompt_version": PROMPT_VERSION,
        "quality_audit": str((args.output_dir / "quality_audit.json").resolve()),
    }
    atomic_write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
