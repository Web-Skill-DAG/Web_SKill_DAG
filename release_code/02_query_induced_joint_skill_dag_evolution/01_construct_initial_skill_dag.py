#!/usr/bin/env python3
"""Serially construct the one-level Initial Skill DAG from Visual Skills."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

import common_openai_api as api


PROMPTS_ROOT = str(Path(__file__).resolve().parents[1])
if PROMPTS_ROOT not in sys.path:
    sys.path.insert(0, PROMPTS_ROOT)

from prompts import INITIAL_SKILL_DAG_CONSTRUCTION_PROMPT as PROMPT


CATEGORY_NAME_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def load_skills(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("categories"), list):
        candidates = [
            skill
            for category in raw["categories"]
            if isinstance(category, dict)
            for skill in category.get("skills", [])
        ]
    elif isinstance(raw, dict) and isinstance(raw.get("skills"), list):
        candidates = raw["skills"]
    elif isinstance(raw, list):
        candidates = raw
    else:
        raise ValueError("library must be a Skill list or contain a skills/categories list")

    by_name: dict[str, dict[str, Any]] = {}
    for index, skill in enumerate(candidates):
        if not isinstance(skill, dict):
            raise ValueError(f"Skill {index} is not an object")
        name = nonempty(skill.get("name"), f"Skill {index}.name")
        nonempty(skill.get("description"), f"Skill {index}.description")
        if name not in by_name:
            by_name[name] = skill
    if not by_name:
        raise ValueError("library contains no Skills")
    return list(by_name.values())


def category_document(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"name", "description"}:
        raise ValueError(f"{field} must contain exactly name and description")
    name = nonempty(value.get("name"), f"{field}.name")
    description = nonempty(value.get("description"), f"{field}.description")
    if not CATEGORY_NAME_RE.fullmatch(name):
        raise ValueError(f"{field}.name must be lowercase_snake_case")
    if "Fits " not in description:
        raise ValueError(f"{field}.description must contain a positive 'Fits ...' sentence")
    return {"name": name, "description": description}


def validate_decision(value: Any, categories: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"routes", "new_category", "rationale"}:
        raise ValueError("decision must contain exactly routes, new_category, and rationale")
    rationale = nonempty(value.get("rationale"), "rationale")
    routes = value.get("routes")
    if not isinstance(routes, list):
        raise ValueError("routes must be a list")

    normalized_routes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, route in enumerate(routes):
        field = f"routes[{index}]"
        if not isinstance(route, dict) or set(route) != {
            "operation", "category_name", "revised_category"
        }:
            raise ValueError(
                f"{field} must contain exactly operation, category_name, and revised_category"
            )
        operation = route.get("operation")
        if operation not in {"select", "revise"}:
            raise ValueError(f"{field}.operation must be select or revise")
        name = nonempty(route.get("category_name"), f"{field}.category_name")
        if name not in categories:
            raise ValueError(f"{field} references unknown Category {name!r}")
        if name in seen:
            raise ValueError(f"Category {name!r} is routed more than once")
        seen.add(name)
        revised = route.get("revised_category")
        if operation == "select":
            if revised is not None:
                raise ValueError(f"{field}.revised_category must be null for select")
        else:
            revised = category_document(revised, f"{field}.revised_category")
            if revised["name"] != name:
                raise ValueError(f"{field} cannot rename an existing Category")
        normalized_routes.append({
            "operation": operation,
            "category_name": name,
            "revised_category": revised,
        })

    new_category = value.get("new_category")
    if new_category is not None:
        new_category = category_document(new_category, "new_category")
        if new_category["name"] in categories:
            raise ValueError("new_category duplicates an existing Category name")
    if not normalized_routes and new_category is None:
        raise ValueError("the incoming Skill must receive at least one Category membership")
    return {
        "routes": normalized_routes,
        "new_category": new_category,
        "rationale": rationale,
    }


def apply_decision(
    decision: dict[str, Any],
    skill_name: str,
    categories: dict[str, dict[str, Any]],
) -> list[str]:
    memberships: list[str] = []
    for route in decision["routes"]:
        name = route["category_name"]
        revised = route["revised_category"]
        if revised is not None:
            categories[name]["description"] = revised["description"]
        if skill_name not in categories[name]["direct_skill_names"]:
            categories[name]["direct_skill_names"].append(skill_name)
        memberships.append(name)
    new_category = decision["new_category"]
    if new_category is not None:
        name = new_category["name"]
        categories[name] = {
            "name": name,
            "description": new_category["description"],
            "parent_names": [],
            "child_names": [],
            "direct_skill_names": [skill_name],
        }
        memberships.append(name)
    return memberships


def prompt_input(skill: dict[str, Any], categories: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "incoming_skill": skill,
        "active_category_index": [
            {"name": node["name"], "description": node["description"]}
            for node in categories.values()
        ],
    }


async def construct(args: argparse.Namespace) -> int:
    skills = load_skills(args.library)
    output = args.output_dir.resolve()
    records = output / "construction_records"
    categories: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []

    for step, skill in enumerate(tqdm(skills, desc="construct Initial Skill DAG", unit="skill"), 1):
        skill_name = nonempty(skill.get("name"), f"Skill {step}.name")
        record = records / f"step-{step:04d}"
        decision_path = record / "decision.json"
        payload = prompt_input(skill, categories)
        atomic_json(record / "input.json", payload)

        if args.resume and decision_path.is_file():
            decision = validate_decision(
                json.loads(decision_path.read_text(encoding="utf-8")), categories
            )
        else:
            prompt = PROMPT + "\n\nInput:\n" + json.dumps(
                payload, ensure_ascii=False, indent=2
            )
            decision, trajectory = await api.retry_direct_dialogue(
                prompt,
                "initial-dag-construction",
                f"step-{step:04d}",
                lambda value: validate_decision(value, categories),
                args,
            )
            atomic_json(record / "trajectory.json", trajectory)
            atomic_json(decision_path, decision)

        memberships = apply_decision(decision, skill_name, categories)
        history.append({
            "step": step,
            "skill_name": skill_name,
            "category_names": memberships,
            "decision": decision,
        })
        atomic_json(record / "state_summary.json", {
            "step": step,
            "skill_name": skill_name,
            "category_names": memberships,
            "active_category_count": len(categories),
        })

    skill_rows = [
        {
            "name": skill["name"],
            "description": skill["description"],
            "canonical_document": skill,
            "aliases": [],
            "merged_into": None,
        }
        for skill in skills
    ]
    membership_count = sum(len(node["direct_skill_names"]) for node in categories.values())
    atomic_json(output / "graph.json", {
        "checkpoint": "query_induced_c0",
        "round": 0,
        "root_node_names": list(categories),
        "nodes": categories,
        "edit_history": [],
    })
    atomic_jsonl(output / "skills.jsonl", skill_rows)
    atomic_jsonl(output / "construction_history.jsonl", history)
    atomic_json(output / "summary.json", {
        "status": "complete",
        "checkpoint": "query_induced_c0",
        "round": 0,
        "root_node_count": len(categories),
        "node_count": len(categories),
        "unique_skill_count": len(skills),
        "active_skill_count": len(skills),
        "membership_count": membership_count,
    })
    api.close_shared_http_adapters()
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Serially construct the one-level Initial Skill DAG."
    )
    value.add_argument("--library", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument(
        "--api-key",
        default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")),
    )
    value.add_argument("--max-output-tokens", type=int, default=4096)
    value.add_argument("--api-timeout", type=float, default=300.0)
    value.add_argument("--retries", type=int, default=2)
    value.add_argument(
        "--retry-forever", action=argparse.BooleanOptionalAction, default=True
    )
    value.add_argument("--retry-delay", type=float, default=5.0)
    value.add_argument("--transport-retries", type=int, default=2)
    value.add_argument("--transport-backoff-factor", type=float, default=1.0)
    value.add_argument("--request-concurrency", type=int, default=1)
    value.add_argument("--max-global-backoff", type=float, default=120.0)
    value.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    return value


def main() -> None:
    raise SystemExit(asyncio.run(construct(parser().parse_args())))


if __name__ == "__main__":
    main()
