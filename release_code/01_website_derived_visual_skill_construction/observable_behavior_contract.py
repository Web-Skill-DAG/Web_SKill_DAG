#!/usr/bin/env python3
"""Visual Goal/Experience/Interaction distillation prompt and schema."""

from __future__ import annotations

import re
from typing import Any

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    DISTILL_PROMPT,
)



def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def string_list(value: Any, field: str, minimum: int = 0) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result = [nonempty(item, f"{field}[]") for item in value]
    if len(result) < minimum:
        raise ValueError(f"{field} needs at least {minimum} entries")
    return result


def api_prompt(template: str) -> str:
    """Collapse JSON braces escaped for the former SDK prompt formatter."""
    return template.format()


def index_name(value: Any, field: str) -> str:
    result = nonempty(value, field)
    if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", result):
        raise ValueError(f"{field} must be lowercase snake_case")
    return result


def pair(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"name", "description"}:
        raise ValueError(f"{field} must contain exactly name and description")
    description = nonempty(value["description"], f"{field}.description")
    if not re.search(r"\bfits\b", description, flags=re.IGNORECASE):
        raise ValueError(f"{field}.description must contain a positive 'Fits ...' applicability statement")
    return {"name": index_name(value["name"], f"{field}.name"), "description": description}


SKILL_FIELDS = {
    "name", "description", "experience_scope", "content_and_composition", "experience_progression",
    "attention_and_information_flow", "interaction_model", "experience_qualities",
    "completion_and_return", "locked_observable_requirements", "adaptable_details",
    "portability_boundary",
}
INTERACTION_FIELDS = {"input", "affected_object", "state_change", "visible_feedback", "control_and_recovery"}


def full_skill(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != SKILL_FIELDS:
        raise ValueError(f"{field} keys must be exactly {sorted(SKILL_FIELDS)}")
    interactions = value["interaction_model"]
    if not isinstance(interactions, list) or not interactions:
        raise ValueError(f"{field}.interaction_model must be a nonempty list")
    validated_interactions = []
    for index, item in enumerate(interactions):
        if not isinstance(item, dict) or set(item) != INTERACTION_FIELDS:
            raise ValueError(f"{field}.interaction_model[{index}] has invalid keys")
        validated_interactions.append({key: nonempty(item[key], f"{field}.interaction_model[{index}].{key}") for key in INTERACTION_FIELDS})
    return {
        "name": index_name(value["name"], f"{field}.name"),
        "description": nonempty(value["description"], f"{field}.description"),
        "experience_scope": nonempty(value["experience_scope"], f"{field}.experience_scope"),
        "content_and_composition": string_list(value["content_and_composition"], f"{field}.content_and_composition", 1),
        "experience_progression": string_list(value["experience_progression"], f"{field}.experience_progression"),
        "attention_and_information_flow": string_list(value["attention_and_information_flow"], f"{field}.attention_and_information_flow", 1),
        "interaction_model": validated_interactions,
        "experience_qualities": string_list(value["experience_qualities"], f"{field}.experience_qualities", 1),
        "completion_and_return": string_list(value["completion_and_return"], f"{field}.completion_and_return", 1),
        "locked_observable_requirements": string_list(value["locked_observable_requirements"], f"{field}.locked_observable_requirements", 1),
        "adaptable_details": string_list(value["adaptable_details"], f"{field}.adaptable_details"),
        "portability_boundary": nonempty(value["portability_boundary"], f"{field}.portability_boundary"),
    }


def validate_distillation(value: Any) -> dict[str, Any]:
    keys = {"case_experience_summary", "category_candidates", "skill_candidates"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"distillation keys must be exactly {sorted(keys)}")
    if not isinstance(value["category_candidates"], list) or not isinstance(value["skill_candidates"], list):
        raise ValueError("candidate fields must be lists")
    categories = [pair(item, f"category_candidates[{index}]") for index, item in enumerate(value["category_candidates"])]
    category_names = [item["name"] for item in categories]
    if len(category_names) != len(set(category_names)):
        raise ValueError("duplicate category candidate names")
    skills = []
    required = SKILL_FIELDS | {"category_candidate_names"}
    for index, item in enumerate(value["skill_candidates"]):
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"skill_candidates[{index}] has invalid keys")
        names = string_list(item["category_candidate_names"], f"skill_candidates[{index}].category_candidate_names", 1)
        if len(names) != len(set(names)) or not set(names).issubset(category_names):
            raise ValueError("invalid category candidate references")
        semantic = full_skill({key: item[key] for key in SKILL_FIELDS}, f"skill_candidates[{index}]")
        skills.append({**semantic, "category_candidate_names": names})
    if len({item["name"] for item in skills}) != len(skills):
        raise ValueError("duplicate skill candidate names")
    referenced = {name for item in skills for name in item["category_candidate_names"]}
    if referenced != set(category_names):
        raise ValueError("every category candidate must contain at least one Skill")
    if bool(categories) != bool(skills):
        raise ValueError("Category and Skill candidate lists must either both be empty or both be non-empty")
    summary = nonempty(value["case_experience_summary"], "case_experience_summary")
    marker = "NO_ELIGIBLE_VISUAL_SKILL"
    if not categories and summary != marker:
        raise ValueError(f"empty candidates require the {marker} marker")
    if categories and summary == marker:
        raise ValueError(f"{marker} cannot accompany emitted candidates")
    return {"case_experience_summary": summary, "category_candidates": categories, "skill_candidates": skills}
