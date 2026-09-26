#!/usr/bin/env python3
"""Shared multi-path Skill-DAG routing and full-document applicability review."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from prompts import DAG_ROUTER_PROMPT, SKILL_APPLICABILITY_REVIEW_PROMPT


JsonRequest = Callable[
    [str, Callable[[Any], dict[str, Any]], str],
    Awaitable[tuple[dict[str, Any], dict[str, Any]]],
]


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def active_skill_name(name: str, skills: dict[str, dict[str, Any]]) -> str:
    seen: set[str] = set()
    while name in skills and skills[name].get("merged_into"):
        if name in seen:
            raise ValueError(f"Skill merge cycle at {name}")
        seen.add(name)
        name = str(skills[name]["merged_into"])
    return name


def validate_graph(graph: dict[str, Any], skills: dict[str, dict[str, Any]]) -> None:
    nodes = graph.get("nodes")
    roots = graph.get("root_node_names")
    if not isinstance(nodes, dict) or not nodes:
        raise ValueError("Skill DAG needs a nonempty nodes object")
    if not isinstance(roots, list) or not roots:
        raise ValueError("Skill DAG needs root_node_names")
    unknown_roots = set(roots) - set(nodes)
    if unknown_roots:
        raise ValueError(f"unknown root nodes: {sorted(unknown_roots)}")
    for name, node in nodes.items():
        children = node.get("child_names", [])
        direct_skills = node.get("direct_skill_names", [])
        if not isinstance(children, list) or not isinstance(direct_skills, list):
            raise ValueError(f"invalid memberships on node {name}")
        unknown_children = set(children) - set(nodes)
        if unknown_children:
            raise ValueError(f"unknown children below {name}: {sorted(unknown_children)}")
        for skill_name in direct_skills:
            canonical = active_skill_name(str(skill_name), skills)
            if canonical not in skills:
                raise ValueError(f"unknown Skill below {name}: {skill_name}")


def route_validator(valid_names: set[str]) -> Callable[[Any], dict[str, Any]]:
    def validate(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"ranking"}:
            raise ValueError("routing response must contain only ranking")
        raw = value["ranking"]
        if not isinstance(raw, list):
            raise ValueError("routing ranking must be a list")
        ranking = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or set(item) != {"name", "reason", "confidence"}:
                raise ValueError(f"routing ranking[{index}] has invalid fields")
            name = _nonempty(item["name"], "routing node name")
            if name not in valid_names or name in seen:
                raise ValueError("routing ranking contains an unknown or duplicate node")
            confidence = item["confidence"]
            if confidence not in {"high", "medium", "low"}:
                raise ValueError("routing confidence must be high, medium, or low")
            seen.add(name)
            ranking.append({
                "rank": len(ranking) + 1,
                "name": name,
                "reason": _nonempty(item["reason"], "routing reason"),
                "confidence": confidence,
            })
        return {"ranking": ranking}

    return validate


def review_validator(valid_names: set[str]) -> Callable[[Any], dict[str, Any]]:
    def validate(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"decisions"}:
            raise ValueError("review response must contain only decisions")
        raw = value["decisions"]
        if not isinstance(raw, list):
            raise ValueError("review decisions must be a list")
        decisions = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or set(item) != {"name", "applicable", "score", "reason"}:
                raise ValueError(f"review decisions[{index}] has invalid fields")
            name = _nonempty(item["name"], "review Skill name")
            if name not in valid_names or name in seen:
                raise ValueError("review decisions contain an unknown or duplicate Skill")
            applicable = item["applicable"]
            score = item["score"]
            if not isinstance(applicable, bool):
                raise ValueError("review applicable must be boolean")
            if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
                raise ValueError("review score must be an integer from 0 to 100")
            seen.add(name)
            decisions.append({
                "name": name,
                "applicable": applicable,
                "score": score,
                "reason": _nonempty(item["reason"], "review reason"),
            })
        if seen != valid_names:
            raise ValueError("review must decide every supplied Skill exactly once")
        return {"decisions": decisions}

    return validate


def _deduplicate(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


async def route_skill_dag(
    query: str,
    graph: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    request_json: JsonRequest,
) -> dict[str, Any]:
    """Recursively follow every matching DAG branch and union direct Skill memberships."""
    validate_graph(graph, skills)
    nodes: dict[str, dict[str, Any]] = graph["nodes"]
    root_names = [str(name) for name in graph["root_node_names"]]
    frontier = root_names
    selected_nodes: list[str] = []
    candidate_names: list[str] = []
    traces: list[dict[str, Any]] = []
    level = 0

    while frontier:
        level += 1
        cards = []
        for name in frontier:
            node = nodes[name]
            cards.append({
                "name": name,
                "description": str(node.get("description", "")),
                "parent_names": list(node.get("parent_names", [])),
            })
        payload = {"query": query, "routing_level": level, "candidate_nodes": cards}
        prompt = DAG_ROUTER_PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False, indent=2)
        decision, trace = await request_json(
            prompt,
            route_validator(set(frontier)),
            f"dag_route_level_{level}",
        )
        chosen = [item["name"] for item in decision["ranking"]]
        traces.append({
            "level": level,
            "candidate_node_names": list(frontier),
            "selected_node_names": chosen,
            "ranking": decision["ranking"],
            "trace": trace,
        })
        selected_nodes.extend(name for name in chosen if name not in selected_nodes)
        next_frontier = []
        for name in chosen:
            node = nodes[name]
            for skill_name in node.get("direct_skill_names", []):
                canonical = active_skill_name(str(skill_name), skills)
                if canonical in skills and not skills[canonical].get("merged_into"):
                    candidate_names.append(canonical)
            next_frontier.extend(str(child) for child in node.get("child_names", []))
        frontier = _deduplicate(next_frontier)

    roots = set(root_names)
    return {
        "selected_node_names": selected_nodes,
        "selected_root_names": [name for name in selected_nodes if name in roots],
        "selected_descendant_names": [name for name in selected_nodes if name not in roots],
        "selected_leaf_node_names": [
            name for name in selected_nodes if nodes[name].get("direct_skill_names")
        ],
        "candidate_skill_names": _deduplicate(candidate_names),
        "routing_traces": traces,
    }


async def review_candidate_skills(
    query: str,
    candidate_names: list[str],
    skills: dict[str, dict[str, Any]],
    request_json: JsonRequest,
    *,
    batch_size: int,
    top_k: int,
) -> dict[str, Any]:
    """Review complete Skill documents, then rank applicable Skills by review score."""
    if batch_size <= 0:
        raise ValueError("review batch size must be positive")
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    batches = [candidate_names[index:index + batch_size] for index in range(0, len(candidate_names), batch_size)]

    async def review_batch(index: int, names: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payload = {"query": query, "skill_documents": [skills[name] for name in names]}
        prompt = (
            SKILL_APPLICABILITY_REVIEW_PROMPT
            + "\n\nInput:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        decision, trace = await request_json(
            prompt,
            review_validator(set(names)),
            f"skill_applicability_review_{index:03d}",
        )
        return decision["decisions"], trace

    reviewed = await asyncio.gather(*[
        review_batch(index, names) for index, names in enumerate(batches, 1)
    ]) if batches else []
    decisions = [decision for batch, _ in reviewed for decision in batch]
    position = {name: index for index, name in enumerate(candidate_names)}
    accepted = [item for item in decisions if item["applicable"]]
    accepted.sort(key=lambda item: (-item["score"], position[item["name"]]))
    ranking = [{
        "rank": index,
        "name": item["name"],
        "reason": item["reason"],
        "score": item["score"],
    } for index, item in enumerate(accepted[:top_k], 1)]
    return {
        "decisions": decisions,
        "ranking": ranking,
        "review_traces": [trace for _, trace in reviewed],
    }


async def retrieve_skills(
    query: str,
    graph: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    request_json: JsonRequest,
    *,
    review_batch_size: int = 8,
    top_k: int = 10,
) -> dict[str, Any]:
    """Run the paper's shared Router -> full-document Reviewer pipeline."""
    routed = await route_skill_dag(query, graph, skills, request_json)
    reviewed = await review_candidate_skills(
        query,
        routed["candidate_skill_names"],
        skills,
        request_json,
        batch_size=review_batch_size,
        top_k=top_k,
    )
    return {**routed, **reviewed}
