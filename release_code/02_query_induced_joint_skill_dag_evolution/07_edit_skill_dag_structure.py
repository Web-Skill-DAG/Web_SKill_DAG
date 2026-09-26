#!/usr/bin/env python3
"""Synthesize and apply one sparse query-induced Skill--DAG revision.

Edits are proposed parent-by-parent from repeated Potential-Query evidence. A split
is concretely represented as removing broad parent memberships and adding the
same Skills to new narrow child nodes. The old broad edges are not retained.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
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
    SKILL_DAG_REVISION_PROMPT as PATCH_PROMPT,
    MERGE_VERIFY_PROMPT,
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


def compact_evidence_for_prompt(
    payload: dict[str, Any],
    taxonomy_names_only: bool = False,
    prompt_max_relations: int = 0,
) -> dict[str, Any]:
    """Remove repeated provenance text without changing the evidence set.

    The full payload remains the source of truth for validation and is written
    to ``input.json``. The API request only needs representative query identifiers
    and explanations for each already-aggregated relation; sending every
    duplicate identifier and near-identical explanation creates very large
    requests without adding a new edit signal.
    """
    compact = deepcopy(payload)
    misses_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in payload["repeated_miss_evidence"]:
        key = (row["operation"], row["source_skill_name"], row["target_name"])
        misses_by_key[key].append(row)
    compact["repeated_miss_evidence"] = [
        row
        for key in sorted(misses_by_key)
        for row in misses_by_key[key][:3]
    ]
    relation_source = payload["repeated_relation_evidence"]
    if prompt_max_relations > 0:
        relation_source = sorted(
            relation_source,
            key=lambda row: (row["relation"] != "alternative_same_position", -int(row["support"]), row["a"], row["b"]),
        )[:prompt_max_relations]
    compact_relations = []
    for row in relation_source:
        item = deepcopy(row)
        item["evidence_query_ids"] = item["evidence_query_ids"][:2]
        # Preserve whether both pair members independently occur as sources,
        # which is needed to decide if a merge is even eligible.
        pair_sources = [name for name in (item["a"], item["b"]) if name in item["source_directions"]]
        item["source_directions"] = pair_sources
        # Skill descriptions and routing distinctions carry the grouping
        # semantics.  Free-form relation reasons are highly repetitive and
        # account for most of the transport payload, so omit them here while
        # retaining them in the full validation payload.
        item["reasons"] = []
        item["routing_distinctions"] = []
        compact_relations.append(item)
    compact["repeated_relation_evidence"] = compact_relations
    compact["involved_skills"] = [{
        "name": row["name"],
        # Family induction needs enough of the Skill itself to recover the
        # trigger--state--feedback--recovery kernel.  The previous 180-char
        # truncation over-weighted names and superficial presentation form.
        "description": "" if taxonomy_names_only else str(row["description"])[:800],
        "is_direct_member_of_parent": row["is_direct_member_of_parent"],
    } for row in payload["involved_skills"]]
    return compact


def node_memberships(graph: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for node_name, node in graph["nodes"].items():
        for skill_name in node.get("direct_skill_names", []):
            result[skill_name].add(node_name)
    return result


def routing_depths(graph: dict[str, Any]) -> dict[str, int]:
    """Return maximum root-relative depth for every reachable routing node."""
    depths = {name: 0 for name in graph["root_node_names"]}
    changed = True
    while changed:
        changed = False
        for parent_name, parent in graph["nodes"].items():
            if parent_name not in depths:
                continue
            for child_name in parent.get("child_names", []):
                candidate = depths[parent_name] + 1
                if candidate > depths.get(child_name, -1):
                    depths[child_name] = candidate
                    changed = True
    return depths


def evidence_by_parent(
    graph: dict[str, Any],
    diagnoses: list[dict[str, Any]],
    skills: dict[str, dict[str, Any]],
    min_support: int,
    max_relations: int,
    split_min_relations: int,
    split_min_skills: int,
    split_move_fraction: float,
    max_splits_per_round: int,
    include_parents_without_direct_skills: bool = False,
) -> dict[str, dict[str, Any]]:
    memberships = node_memberships(graph)
    depths = routing_depths(graph)
    misses: dict[str, list[dict[str, Any]]] = defaultdict(list)
    relations: dict[str, dict[tuple[str, str, str], list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    for row in diagnoses:
        miss = row["miss_attribution"]
        operation = miss["operation"]
        source = row["source_skill_name"]
        if operation in {"rewrite_node", "add_edge"}:
            parents = [miss["target_name"]]
        elif operation == "rewrite_skill":
            parents = sorted(memberships.get(source, []))
        else:
            parents = []
        for parent in parents:
            if parent in graph["nodes"]:
                misses[parent].append({
                    "query_id": row["query_id"],
                    "potential_query": row["potential_query"],
                    "source_skill_name": source,
                    **miss,
                })
        for relation in row["relations"]:
            if relation["relation"] == "alternative_same_position":
                common = memberships.get(relation["a"], set()) & memberships.get(relation["b"], set())
                if common:
                    # Once a prior split places both alternatives inside a
                    # learned family, the unresolved distinction belongs to
                    # that deepest shared family rather than to the stale C0
                    # parent named by the earlier diagnosis.
                    deepest = max(depths.get(name, -1) for name in common)
                    parents = sorted(name for name in common if depths.get(name, -1) == deepest)
                else:
                    parent = relation["split_parent"]
                    if parent not in graph["nodes"]:
                        continue
                    parents = [parent]
            else:
                common = memberships.get(relation["a"], set()) & memberships.get(relation["b"], set())
                # A cross-root `same` judgement is important evidence of
                # fragmentation introduced by the initial coarse categories.
                # Exposing it to both current parents lets either evidence-rich
                # partition recover the shared family; dropping it makes such
                # repair impossible by construction.
                if common:
                    deepest = max(depths.get(name, -1) for name in common)
                    parents = sorted(name for name in common if depths.get(name, -1) == deepest)
                else:
                    parents = sorted(
                        memberships.get(relation["a"], set())
                        | memberships.get(relation["b"], set())
                    )
                if not parents:
                    continue
            key = (relation["a"], relation["b"], relation["relation"])
            for parent in parents:
                if parent not in graph["nodes"]:
                    continue
                relations[parent][key].append({
                    "query_id": row["query_id"],
                    "source_skill_name": source,
                    "reason": relation["reason"],
                    "routing_distinction": relation["routing_distinction"],
                })

    output: dict[str, dict[str, Any]] = {}
    split_eligible: list[tuple[int, str]] = []
    # Every routing node with direct members is optimizable. Restricting this
    # loop to roots prevents newly created children from ever being refined in
    # later rounds and artificially caps the graph depth.
    for parent, parent_node in graph["nodes"].items():
        if not parent_node.get("direct_skill_names") and not include_parents_without_direct_skills:
            continue
        parent_misses = misses.get(parent, [])
        relation_rows = []
        involved: set[str] = {row["source_skill_name"] for row in parent_misses}
        sorted_relations = sorted(
            relations.get(parent, {}).items(),
            key=lambda item: (-len({row["query_id"] for row in item[1]}), item[0]),
        )
        for (a, b, relation), rows in sorted_relations:
            query_ids = sorted({row["query_id"] for row in rows})
            source_directions = sorted({row["source_skill_name"] for row in rows})
            if len(query_ids) < min_support:
                continue
            relation_rows.append({
                "a": a,
                "b": b,
                "relation": relation,
                "support": len(query_ids),
                "source_directions": source_directions,
                "evidence_query_ids": query_ids,
                "reasons": [row["reason"] for row in rows[:3]],
                "routing_distinctions": sorted({row["routing_distinction"] for row in rows if row["routing_distinction"]})[:5],
            })
            involved.update((a, b))
            if len(relation_rows) >= max_relations:
                break

        # Repeated miss operations are exposed; singleton rescue suggestions are
        # omitted before the evidence is sent.
        miss_counter = Counter((row["operation"], row["source_skill_name"], row["target_name"]) for row in parent_misses)
        retained_misses = [
            row for row in parent_misses
            if miss_counter[(row["operation"], row["source_skill_name"], row["target_name"])] >= min_support
        ]
        if not retained_misses and not relation_rows:
            continue
        direct = set(graph["nodes"][parent].get("direct_skill_names", []))
        retained_alternatives = [
            row for row in relation_rows if row["relation"] == "alternative_same_position"
        ]
        # Alternative evidence can connect Skills that started in different
        # top-level categories.  Such cross-root evidence is precisely where a
        # multi-parent DAG is useful: a non-direct Skill may be attached to a
        # newly induced child without deleting its other valid membership.
        alternative_candidates = sorted({
            name
            for row in retained_alternatives
            for name in (row["a"], row["b"])
            if name in skills
        })
        # Once alternatives establish that the parent is over-broad, jointly
        # repartition every evidence-linked Skill.  `same` neighbours are what
        # allow the new children to become coherent small families instead of
        # arbitrary bins around alternative pairs.
        split_candidates = sorted({
            name
            for row in relation_rows
            for name in (row["a"], row["b"])
            if name in skills
        })
        split_is_eligible = (
            len(retained_alternatives) >= split_min_relations
            and len(alternative_candidates) >= split_min_skills
        )
        if split_is_eligible:
            split_eligible.append((sum(row["support"] for row in retained_alternatives), parent))
        minimum_split_move_count = (
            max(split_min_skills, math.ceil(len(alternative_candidates) * split_move_fraction))
            if split_is_eligible else 0
        )
        output[parent] = {
            "parent": {
                "name": parent,
                "description": graph["nodes"][parent]["description"],
                "direct_skill_count": len(direct),
            },
            "split_required": split_is_eligible,
            "minimum_split_move_count": minimum_split_move_count,
            "split_candidate_skill_names": split_candidates,
            "repeated_miss_evidence": retained_misses,
            "repeated_relation_evidence": relation_rows,
            "involved_skills": [
                {
                    "name": name,
                    "description": skills[name]["description"],
                    "is_direct_member_of_parent": name in direct,
                    "all_current_memberships": sorted(memberships.get(name, [])),
                }
                for name in sorted(involved) if name in skills
            ],
        }
    # Structural change is deliberately incremental: only the strongest few
    # parents are forced to split in one round, even if many are eligible.
    ordered_split_parents = [parent for _score, parent in sorted(split_eligible, reverse=True)]
    selected_split_parents = set(
        ordered_split_parents
        if max_splits_per_round <= 0
        else ordered_split_parents[:max_splits_per_round]
    )
    for parent, payload in output.items():
        payload["split_required"] = parent in selected_split_parents
        if not payload["split_required"]:
            payload["minimum_split_move_count"] = 0
    return output


def validate_proposal(value: Any, payload: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    keys = {"parent_rewrite", "skill_rewrites", "add_edges", "merges", "splits"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid proposal top-level shape")
    parent = payload["parent"]["name"]
    supplied_skills = {row["name"] for row in payload["involved_skills"]}
    direct_members = {row["name"] for row in payload["involved_skills"] if row["is_direct_member_of_parent"]}
    valid_queries = {
        row["query_id"] for row in payload["repeated_miss_evidence"]
    } | {
        query_id for row in payload["repeated_relation_evidence"] for query_id in row["evidence_query_ids"]
    }
    miss_query_ids: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in payload["repeated_miss_evidence"]:
        miss_query_ids[(row["operation"], row["source_skill_name"], row["target_name"])].add(row["query_id"])
    alternative_rows = [row for row in payload["repeated_relation_evidence"] if row["relation"] == "alternative_same_position"]
    alternative_query_ids = {query_id for row in alternative_rows for query_id in row["evidence_query_ids"]}
    alternative_skills = {name for row in alternative_rows for name in (row["a"], row["b"])}
    split_candidates = set(payload["split_candidate_skill_names"])

    parent_rewrite = value["parent_rewrite"]
    if parent_rewrite is not None:
        if not isinstance(parent_rewrite, dict) or set(parent_rewrite) != {"replacement_description", "evidence_query_ids"}:
            raise ValueError("invalid parent_rewrite")
        if not str(parent_rewrite["replacement_description"]).strip():
            raise ValueError("empty parent replacement")

    def evidence_ids(item: dict[str, Any]) -> list[str]:
        ids = item.get("evidence_query_ids")
        if not isinstance(ids, list) or len(set(ids)) < 2 or not set(ids) <= valid_queries:
            raise ValueError("edit requires at least two supplied evidence queries")
        return list(dict.fromkeys(str(item_id) for item_id in ids))

    skill_rewrites = []
    for item in value["skill_rewrites"]:
        if set(item) != {"skill_name", "replacement_description", "evidence_query_ids"} or item["skill_name"] not in supplied_skills:
            raise ValueError("invalid skill rewrite")
        if not str(item["replacement_description"]).strip():
            raise ValueError("empty Skill replacement")
        ids = evidence_ids(item)
        allowed = miss_query_ids[("rewrite_skill", item["skill_name"], item["skill_name"])]
        if not set(ids) <= allowed:
            raise ValueError("Skill rewrite cites unrelated miss evidence")
        skill_rewrites.append({**item, "evidence_query_ids": ids})

    add_edges = []
    for item in value["add_edges"]:
        if set(item) != {"skill_name", "target_node", "evidence_query_ids"}:
            raise ValueError("invalid add edge")
        if item["skill_name"] not in supplied_skills or item["target_node"] != parent:
            raise ValueError("add edge target out of scope")
        ids = evidence_ids(item)
        allowed = miss_query_ids[("add_edge", item["skill_name"], parent)]
        if not set(ids) <= allowed:
            # The structured proposal may contain useful rewrites or merge
            # candidates alongside an over-interpreted `same` relation.  The
            # evidence gate rejects only the unsupported atomic operation;
            # repeatedly regenerating the whole response changes no evidence
            # and needlessly spends API calls.
            continue
        add_edges.append({**item, "evidence_query_ids": ids})

    merges = []
    for item in value["merges"]:
        if set(item) != {"keep_skill", "remove_skill", "evidence_query_ids"}:
            raise ValueError("invalid merge")
        if item["keep_skill"] not in supplied_skills or item["remove_skill"] not in supplied_skills or item["keep_skill"] == item["remove_skill"]:
            raise ValueError("merge out of scope")
        ids = evidence_ids(item)
        pair = tuple(sorted((item["keep_skill"], item["remove_skill"])))
        matching = [
            row for row in payload["repeated_relation_evidence"]
            if row["relation"] == "same" and tuple(sorted((row["a"], row["b"]))) == pair
        ]
        # Additional source Skills may co-retrieve and independently support the
        # same pair.  Bidirectional evidence means that both members of the pair
        # occur as sources, not that they are the only observed sources.
        if not matching or not set(pair) <= set(matching[0]["source_directions"]):
            raise ValueError("merge requires repeated bidirectional same evidence")
        if not set(ids) <= set(matching[0]["evidence_query_ids"]):
            raise ValueError("merge cites unrelated evidence")
        merges.append({**item, "evidence_query_ids": ids})

    splits = []
    if not payload.get("splits_allowed", True) and value["splits"]:
        raise ValueError("splits are disabled for this staged non-structural synthesis")
    existing_names = set(graph["nodes"])
    generated_names: set[str] = set()
    all_moved: set[str] = set()
    for item in value["splits"]:
        if set(item) != {"parent_node", "children", "evidence_query_ids"} or item["parent_node"] != parent:
            raise ValueError("invalid split")
        ids = evidence_ids(item)
        if not isinstance(item["children"], list) or not 2 <= len(item["children"]) <= 8:
            raise ValueError("split must create 2-8 children")
        moved: set[str] = set()
        children = []
        for child in item["children"]:
            if set(child) != {"name", "description", "move_skill_names"}:
                raise ValueError("invalid child")
            name = str(child["name"])
            if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", name) or name in existing_names or name in generated_names:
                raise ValueError("child name must be new snake_case")
            if not str(child["description"]).strip() or "fits" not in str(child["description"]).lower():
                raise ValueError("child description must be non-empty and include a Fits sentence")
            move_names = list(dict.fromkeys(child["move_skill_names"]))
            if len(move_names) < 2 or not set(move_names) <= supplied_skills or moved & set(move_names) or all_moved & set(move_names):
                raise ValueError("split may place each supplied evidence-linked Skill once")
            moved.update(move_names)
            all_moved.update(move_names)
            generated_names.add(name)
            children.append({"name": name, "description": str(child["description"]), "move_skill_names": move_names})
        required_move_count = max(4, int(payload["minimum_split_move_count"]))
        if len(moved) < required_move_count:
            raise ValueError(f"split must move at least {required_move_count} Skills")
        if not moved <= split_candidates or len(set(ids) & alternative_query_ids) < 2:
            raise ValueError("split must cite at least two alternative-evidence Queries and place only relation-linked Skills")
        splits.append({"parent_node": parent, "children": children, "evidence_query_ids": ids})

    if payload["split_required"] and len(splits) != 1:
        raise ValueError("dense repeated alternative evidence requires exactly one structural split")

    if parent_rewrite is not None:
        ids = evidence_ids(parent_rewrite)
        allowed = set().union(*(
            query_ids for (operation, _source, target), query_ids in miss_query_ids.items()
            if operation == "rewrite_node" and target == parent
        )) if any(operation == "rewrite_node" and target == parent for operation, _source, target in miss_query_ids) else set()
        if not set(ids) <= allowed:
            raise ValueError("parent rewrite cites unrelated miss evidence")
        parent_rewrite = {**parent_rewrite, "evidence_query_ids": ids}
    return {
        "parent_rewrite": parent_rewrite,
        "skill_rewrites": skill_rewrites,
        "add_edges": add_edges,
        "merges": merges,
        "splits": splits,
    }


async def verify_merge(item: dict[str, Any], skills: dict[str, dict[str, Any]], args: argparse.Namespace, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    a, b = sorted((item["keep_skill"], item["remove_skill"]))
    record_dir = args.output_dir / "merge_verifications" / f"{a}__{b}"
    result_path = record_dir / "result.json"
    payload = {
        "skill_a": {"name": a, "canonical_document": skills[a]["canonical_document"]},
        "skill_b": {"name": b, "canonical_document": skills[b]["canonical_document"]},
        "query_evidence_ids": item["evidence_query_ids"],
    }
    input_path = record_dir / "input.json"
    if args.resume and result_path.exists() and input_path.exists():
        cached_payload = json.loads(input_path.read_text(encoding="utf-8"))
        if cached_payload == payload:
            return json.loads(result_path.read_text(encoding="utf-8"))
    record_dir.mkdir(parents=True, exist_ok=True)
    prompt = MERGE_VERIFY_PROMPT + "\n\nInput:\n" + json.dumps(payload, ensure_ascii=False)
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
            if not isinstance(value, dict) or set(value) != {"equivalent", "keep_skill", "reason"}:
                raise ValueError("invalid merge verification shape")
            equivalent = value["equivalent"]
            keep = str(value["keep_skill"])
            if not isinstance(equivalent, bool):
                raise ValueError("equivalent must be boolean")
            if equivalent and keep not in {a, b}:
                raise ValueError("equivalent merge must choose a supplied keep Skill")
            if not equivalent and keep:
                raise ValueError("rejected merge keep_skill must be empty")
            result = {
                "skill_pair": [a, b],
                "equivalent": equivalent,
                "keep_skill": keep,
                "reason": str(value["reason"]),
                "evidence_query_ids": item["evidence_query_ids"],
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
            correction = f"\n\nPrevious output was invalid: {error}. Return corrected JSON only."
            await asyncio.sleep(args.retry_delay)


async def propose_one(parent: str, payload: dict[str, Any], graph: dict[str, Any], args: argparse.Namespace, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    record_dir = args.output_dir / "proposals" / parent
    result_path = record_dir / "result.json"
    input_path = record_dir / "input.json"
    if args.resume and result_path.exists() and input_path.exists():
        cached_payload = json.loads(input_path.read_text(encoding="utf-8"))
        if cached_payload == payload:
            return json.loads(result_path.read_text(encoding="utf-8"))
        # Evidence or split-selection settings changed. A cached proposal for
        # another payload is not a resumable result and must be regenerated.
    record_dir.mkdir(parents=True, exist_ok=True)
    prompt_payload = compact_evidence_for_prompt(
        payload, args.taxonomy_names_only, args.prompt_max_relations
    ) if args.compact_evidence_prompt else payload
    atomic_json(record_dir / "prompt_input.json", prompt_payload)
    prompt = PATCH_PROMPT + "\n\nInput:\n" + json.dumps(prompt_payload, ensure_ascii=False)
    if args.partition_only:
        prompt += (
            "\n\nThis invocation is strictly partition-only. Return parent_rewrite=null "
            "and empty skill_rewrites, add_edges, and merges arrays. Emit exactly one valid "
            "split, place every moved Skill no more than once, and do not propose any merge."
        )
    attempts: list[dict[str, Any]] = []
    correction = ""
    while True:
        try:
            async with semaphore:
                request, response, status, _ = await asyncio.to_thread(
                    api.call_chat_api, prompt + correction, args, str(uuid.uuid4())
                )
            text = api.response_text(response)
            proposal = validate_proposal(api.extract_json_object(text), payload, graph)
            result = {
                "parent_name": parent,
                "proposal": proposal,
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
            correction = f"\n\nPrevious output was invalid: {error}. Return corrected JSON only."
            if "merge requires repeated bidirectional same evidence" in str(error):
                correction += " The unsupported merge must be removed; return merges=[] for this correction."
            await asyncio.sleep(args.retry_delay)


def apply_proposals(graph: dict[str, Any], skills: dict[str, dict[str, Any]], proposals: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    next_graph = deepcopy(graph)
    next_skills = deepcopy(skills)
    edits: list[dict[str, Any]] = []
    removed_by_merge: set[str] = set()
    rewritten_skills: set[str] = set()

    for row in proposals:
        parent = row["parent_name"]
        proposal = row["proposal"]
        if proposal["parent_rewrite"] is not None:
            item = proposal["parent_rewrite"]
            before = next_graph["nodes"][parent]["description"]
            next_graph["nodes"][parent]["description"] = item["replacement_description"]
            edits.append({"operation": "rewrite_node", "target": parent, "before": before, "after": item["replacement_description"], "evidence_query_ids": item["evidence_query_ids"]})

        for item in proposal["skill_rewrites"]:
            name = item["skill_name"]
            if name in rewritten_skills or name in removed_by_merge:
                continue
            before = next_skills[name]["description"]
            next_skills[name]["description"] = item["replacement_description"]
            rewritten_skills.add(name)
            edits.append({"operation": "rewrite_skill", "target": name, "before": before, "after": item["replacement_description"], "evidence_query_ids": item["evidence_query_ids"]})

        for item in proposal["add_edges"]:
            name, target = item["skill_name"], item["target_node"]
            members = next_graph["nodes"][target]["direct_skill_names"]
            if name not in members and name not in removed_by_merge:
                members.append(name)
                edits.append({"operation": "add_edge", "skill_name": name, "target_node": target, "evidence_query_ids": item["evidence_query_ids"]})

        for item in proposal["merges"]:
            keep, remove = item["keep_skill"], item["remove_skill"]
            if keep in removed_by_merge or remove in removed_by_merge:
                continue
            # A cumulative Q/Skill run may propose a merge that an earlier
            # checkpoint has already applied.  Treat that as an idempotent
            # no-op instead of appending the same alias a second time.
            if next_skills[remove].get("merged_into"):
                continue
            if next_skills[keep].get("merged_into"):
                continue
            next_skills[remove]["merged_into"] = keep
            aliases = next_skills[keep].setdefault("aliases", [])
            if remove not in aliases:
                aliases.append(remove)
            for node in next_graph["nodes"].values():
                members = node.get("direct_skill_names", [])
                if remove in members:
                    node["direct_skill_names"] = list(dict.fromkeys(keep if name == remove else name for name in members))
            removed_by_merge.add(remove)
            edits.append({"operation": "merge", "keep_skill": keep, "remove_skill": remove, "evidence_query_ids": item["evidence_query_ids"]})

        for item in proposal["splits"]:
            parent_node = next_graph["nodes"][parent]
            for child in item["children"]:
                child_name = child["name"]
                if child_name in next_graph["nodes"]:
                    base_name = f"{parent}_{child_name}"
                    child_name = base_name
                    suffix = 2
                    while child_name in next_graph["nodes"]:
                        child_name = f"{base_name}_{suffix}"
                        suffix += 1
                move_names = [name for name in child["move_skill_names"] if name not in removed_by_merge]
                # The structural split is exactly remove broad edge + add narrow edge.
                parent_node["direct_skill_names"] = [name for name in parent_node["direct_skill_names"] if name not in set(move_names)]
                next_graph["nodes"][child_name] = {
                    "name": child_name,
                    "description": child["description"],
                    "parent_names": [parent],
                    "child_names": [],
                    "direct_skill_names": move_names,
                }
                parent_node.setdefault("child_names", []).append(child_name)
                for name in move_names:
                    edits.append({
                        "operation": "split_rewire",
                        "remove_edge": {"node": parent, "skill": name},
                        "add_edge": {"node": child_name, "skill": name},
                        "evidence_query_ids": item["evidence_query_ids"],
                    })

    next_graph["round"] = int(graph.get("round", 0)) + 1
    next_graph["checkpoint"] = f"query_induced_round{next_graph['round']}"
    next_graph.setdefault("edit_history", []).append({
        "from_checkpoint": graph["checkpoint"],
        "to_checkpoint": next_graph["checkpoint"],
        "edits": edits,
    })
    return next_graph, next_skills, edits


async def main(args: argparse.Namespace) -> None:
    if not args.api_key:
        raise ValueError("QUERY_API_KEY, API_KEY, or --api-key is required")
    if args.partition_only and args.non_structural_only:
        raise ValueError("--partition-only and --non-structural-only are mutually exclusive")
    graph_path = args.checkpoint / "graph.json"
    skills_path = args.checkpoint / "skills.jsonl"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    skills = {row["name"]: row for row in read_jsonl(skills_path)}
    diagnoses = read_jsonl(args.diagnosis_results)
    evidence = evidence_by_parent(
        graph,
        diagnoses,
        skills,
        args.min_support,
        args.max_relations_per_parent,
        args.split_min_relations,
        args.split_min_skills,
        args.split_move_fraction,
        args.max_splits_per_round,
        args.non_structural_only,
    )
    atomic_json(args.output_dir / "aggregated_evidence_full.json", evidence)
    if args.non_structural_only:
        # The family topology was already induced and completed from this same
        # C0 diagnosis. Retain only the evidence that can justify the
        # edit types suppressed by --partition-only: repeated misses for node/
        # Skill rewrites and membership additions, plus repeated bidirectional
        # `same` relations for conservatively verified merges.  This is staged
        # execution of one evidence snapshot, not C1 re-diagnosis.
        non_structural: dict[str, dict[str, Any]] = {}
        for parent, payload in evidence.items():
            item = deepcopy(payload)
            item["repeated_relation_evidence"] = [
                row for row in item["repeated_relation_evidence"]
                if row["relation"] == "same"
            ]
            if not item["repeated_miss_evidence"] and not item["repeated_relation_evidence"]:
                continue
            involved_names = {
                row["source_skill_name"] for row in item["repeated_miss_evidence"]
            } | {
                name
                for row in item["repeated_relation_evidence"]
                for name in (row["a"], row["b"])
            }
            item["involved_skills"] = [
                row for row in item["involved_skills"] if row["name"] in involved_names
            ]
            item["split_required"] = False
            item["splits_allowed"] = False
            item["minimum_split_move_count"] = 0
            item["split_candidate_skill_names"] = []
            non_structural[parent] = item
        evidence = non_structural
    if args.partition_seed_skill_cap > 0:
        seeded: dict[str, dict[str, Any]] = {}
        for parent, payload in evidence.items():
            if not payload["split_required"]:
                seeded[parent] = payload
                continue
            relations = payload["repeated_relation_evidence"]
            alternatives = sorted(
                (row for row in relations if row["relation"] == "alternative_same_position"),
                key=lambda row: (-int(row["support"]), row["a"], row["b"]),
            )
            same_rows = sorted(
                (row for row in relations if row["relation"] == "same"),
                key=lambda row: (-int(row["support"]), row["a"], row["b"]),
            )
            selected: list[dict[str, Any]] = []
            selected_keys: set[tuple[str, str, str]] = set()
            names: set[str] = set()

            def add_if_fits(row: dict[str, Any], skill_limit: int, require_touch: bool = False) -> bool:
                key = (row["a"], row["b"], row["relation"])
                endpoints = {row["a"], row["b"]}
                if key in selected_keys or (require_touch and names and not endpoints & names):
                    return False
                if len(names | endpoints) > skill_limit:
                    return False
                selected.append(row)
                selected_keys.add(key)
                names.update(endpoints)
                return True

            # Reserve roughly half of the seed budget for the strongest
            # distinctions, then grow same-mechanism neighbourhoods around
            # those endpoints.  This produces family skills without one
            # enormous all-Skill request; remaining Skills are assigned later
            # by complete_child_partitions.py in resumable batches.
            alternative_cap = max(4, args.partition_seed_skill_cap // 2)
            for row in alternatives:
                add_if_fits(row, alternative_cap)
            for row in same_rows:
                add_if_fits(row, args.partition_seed_skill_cap, require_touch=True)
            for row in alternatives + same_rows:
                add_if_fits(row, args.partition_seed_skill_cap)
            if len(names) < 4 or sum(row["relation"] == "alternative_same_position" for row in selected) == 0:
                raise ValueError(f"insufficient partition seed evidence for {parent}")
            item = deepcopy(payload)
            item["repeated_relation_evidence"] = selected
            item["split_candidate_skill_names"] = sorted(names)
            item["involved_skills"] = [
                row for row in payload["involved_skills"] if row["name"] in names
            ]
            item["minimum_split_move_count"] = min(
                len(names), max(4, int(payload["minimum_split_move_count"] or 4))
            )
            if args.partition_only:
                item["repeated_miss_evidence"] = []
            seeded[parent] = item
        evidence = seeded
    if args.partition_only:
        # In a structural-only pass, proposals for parents below the registered
        # split threshold cannot change the checkpoint: rewrites, memberships,
        # and merges are intentionally discarded later. Do not spend an API
        # call asking those parents to return an inevitable no-op.  This is a
        # transport/cost optimization only; the eligibility rule and every
        # accepted structural operation are unchanged.
        evidence = {
            parent: payload for parent, payload in evidence.items()
            if payload["split_required"]
        }
    atomic_json(args.output_dir / "aggregated_evidence.json", evidence)

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [asyncio.create_task(propose_one(parent, payload, graph, args, semaphore)) for parent, payload in evidence.items()]
    proposals: list[dict[str, Any]] = []
    bar = tqdm(total=len(tasks), desc="synthesize sparse patch", unit="parent")
    for task in asyncio.as_completed(tasks):
        proposals.append(await task)
        bar.update(1)
    bar.close()
    node_order = {name: index for index, name in enumerate(graph["nodes"])}
    proposals.sort(key=lambda row: node_order[row["parent_name"]])
    if args.partition_only:
        # Seeded family induction is the structural partition stage. The
        # response may contain plausible rewrites, memberships, or equivalent
        # Skills while comparing the seed set, but accepting those operations
        # would conflate partition quality with identity/document changes and
        # would trigger a second set of merge-verification calls.  Preserve the
        # raw response in each proposal's attempt record, while applying only
        # the evidence-induced split in this mode.  A proposed same-pair merge
        # is converted to co-membership in the keep side's learned family when
        # the other side was omitted from the split.  This preserves both Skill
        # identities while retaining the mechanism-family judgment.
        for row in proposals:
            proposal = row["proposal"]
            candidate_names = set(evidence[row["parent_name"]]["split_candidate_skill_names"])
            placement: dict[str, dict[str, Any]] = {}
            for split in proposal["splits"]:
                for child in split["children"]:
                    for skill_name in child["move_skill_names"]:
                        placement[skill_name] = child
            for merge in proposal["merges"]:
                keep_name = merge["keep_skill"]
                remove_name = merge["remove_skill"]
                keep_child = placement.get(keep_name)
                remove_child = placement.get(remove_name)
                if keep_child is not None and remove_child is None and remove_name in candidate_names:
                    keep_child["move_skill_names"].append(remove_name)
                    placement[remove_name] = keep_child
                elif remove_child is not None and keep_child is None and keep_name in candidate_names:
                    remove_child["move_skill_names"].append(keep_name)
                    placement[keep_name] = remove_child
                elif keep_child is not None and remove_child is not None and keep_child is not remove_child:
                    raise ValueError(
                        f"same-pair endpoints placed in different families under {row['parent_name']}: "
                        f"{keep_name}, {remove_name}"
                    )
            proposal["parent_rewrite"] = None
            proposal["skill_rewrites"] = []
            proposal["add_edges"] = []
            proposal["merges"] = []
    atomic_jsonl(args.output_dir / "parent_proposals_pre_merge_verification.jsonl", proposals)

    merge_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for row in proposals:
        for item in row["proposal"]["merges"]:
            pair = tuple(sorted((item["keep_skill"], item["remove_skill"])))
            if pair not in merge_by_pair:
                merge_by_pair[pair] = deepcopy(item)
            else:
                merge_by_pair[pair]["evidence_query_ids"] = sorted(set(
                    merge_by_pair[pair]["evidence_query_ids"] + item["evidence_query_ids"]
                ))
    merge_tasks = [asyncio.create_task(verify_merge(item, skills, args, semaphore)) for item in merge_by_pair.values()]
    merge_verifications: list[dict[str, Any]] = []
    merge_bar = tqdm(total=len(merge_tasks), desc="verify merge candidates", unit="pair")
    for task in asyncio.as_completed(merge_tasks):
        merge_verifications.append(await task)
        merge_bar.update(1)
    merge_bar.close()
    verification_by_pair = {
        tuple(row["skill_pair"]): row for row in merge_verifications
    }
    for row in proposals:
        accepted_merges = []
        for item in row["proposal"]["merges"]:
            pair = tuple(sorted((item["keep_skill"], item["remove_skill"])))
            verification = verification_by_pair[pair]
            if not verification["equivalent"]:
                continue
            keep = verification["keep_skill"]
            remove = pair[1] if keep == pair[0] else pair[0]
            accepted_merges.append({
                "keep_skill": keep,
                "remove_skill": remove,
                "evidence_query_ids": item["evidence_query_ids"],
            })
        row["proposal"]["merges"] = accepted_merges
    atomic_jsonl(args.output_dir / "parent_proposals.jsonl", proposals)
    atomic_jsonl(args.output_dir / "merge_verifications.jsonl", merge_verifications)

    next_graph, next_skills, edits = apply_proposals(graph, skills, proposals)
    checkpoint_dir = args.output_dir / "checkpoint"
    atomic_json(checkpoint_dir / "graph.json", next_graph)
    atomic_jsonl(checkpoint_dir / "skills.jsonl", [next_skills[name] for name in skills])
    operation_counts = Counter(edit["operation"] for edit in edits)
    active_skills = sum(not row.get("merged_into") for row in next_skills.values())
    membership_count = sum(len(node.get("direct_skill_names", [])) for node in next_graph["nodes"].values())
    total_input = sum(row["usage"]["input_tokens"] for row in proposals) + sum(row["usage"]["input_tokens"] for row in merge_verifications)
    total_output = sum(row["usage"]["output_tokens"] for row in proposals) + sum(row["usage"]["output_tokens"] for row in merge_verifications)
    atomic_json(checkpoint_dir / "summary.json", {
        "status": "complete",
        "checkpoint": next_graph["checkpoint"],
        "round": next_graph["round"],
        "parent_proposal_count": len(proposals),
        "merge_candidate_count": len(merge_verifications),
        "verified_merge_count": sum(row["equivalent"] for row in merge_verifications),
        "operation_counts": dict(sorted(operation_counts.items())),
        "edit_record_count": len(edits),
        "root_node_count": len(next_graph["root_node_names"]),
        "node_count": len(next_graph["nodes"]),
        "unique_skill_identity_count": len(next_skills),
        "active_skill_count": active_skills,
        "membership_count": membership_count,
        "synthesis_input_tokens": total_input,
        "synthesis_output_tokens": total_output,
        "split_semantics": "remove broad parent-to-Skill edge plus add narrow child-to-Skill edge",
    })
    atomic_jsonl(checkpoint_dir / "edit_patch.jsonl", edits)
def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument("--checkpoint", type=Path, required=True)
    value.add_argument("--diagnosis-results", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, required=True)
    value.add_argument("--min-support", type=int, default=2)
    value.add_argument("--max-relations-per-parent", type=int, default=80)
    value.add_argument("--split-min-relations", type=int, default=8)
    value.add_argument("--split-min-skills", type=int, default=12)
    value.add_argument("--split-move-fraction", type=float, default=0.6)
    value.add_argument("--max-splits-per-round", type=int, default=2,
                       help="Maximum split parents per round; 0 keeps every evidence-eligible split")
    value.add_argument("--partition-seed-skill-cap", type=int, default=0,
                       help="Induce split boundaries from at most this many evidence-linked seed Skills; 0 disables")
    value.add_argument("--partition-only", action=argparse.BooleanOptionalAction, default=False,
                       help="During seeded family induction, suppress miss-driven rewrites and membership additions")
    value.add_argument("--non-structural-only", action=argparse.BooleanOptionalAction, default=False,
                       help="Consume the same diagnosis evidence for rewrites, additions, and verified merges, but forbid further splits")
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--concurrency", type=int, default=11)
    value.add_argument("--request-concurrency", type=int, default=11)
    value.add_argument("--api-timeout", type=int, default=1800)
    value.add_argument("--max-output-tokens", type=int, default=16384)
    value.add_argument("--compact-evidence-prompt", action=argparse.BooleanOptionalAction, default=False,
                       help="Send representative provenance for each aggregated signal; full evidence still validates edits")
    value.add_argument("--taxonomy-names-only", action=argparse.BooleanOptionalAction, default=False,
                       help="Transport fallback: discover child semantics from descriptive Skill names; full descriptions return during partition assignment")
    value.add_argument("--prompt-max-relations", type=int, default=0,
                       help="Transport-only relation summary cap; 0 sends every aggregated relation while full evidence always validates edits")
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
