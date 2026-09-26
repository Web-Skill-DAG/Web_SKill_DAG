#!/usr/bin/env python3
"""Generate source-grounded, skill-scoped reusable Web Skills with Claude SDK."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import shutil
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import ClaudeAgentOptions, query
from tqdm import tqdm

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    CONSTRUCT_VISUAL_SKILLS_PROMPT as PROMPT,
)



SKILL_FIELDS = {
    "name",
    "description",
    "experience_scope",
    "content_and_composition",
    "experience_progression",
    "attention_and_information_flow",
    "interaction_model",
    "experience_qualities",
    "completion_and_return",
    "locked_observable_requirements",
    "adaptable_details",
    "portability_boundary",
}

REQUIRED_SECTIONS = [
    "## Use this skill when",
    "## Core mechanism",
    "## Implementation workflow",
    "## State and update model",
    "## Constraints and invariants",
    "## Adaptation points",
    "## Validation",
]

FORBIDDEN_APPLICATION_TERMS = re.compile(
    r"\b(?:React(?:\.js)?|Vue(?:\.js)?|Svelte(?:Kit)?|Angular|Astro|JSX|TSX|SSR|CSR|SSG|Vite|Webpack|Parcel)\b",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Candidate:
    case_id: str
    skill_index: int
    skill: dict[str, Any]

    @property
    def key(self) -> str:
        return f"{self.case_id}__s{self.skill_index:02d}"


@dataclasses.dataclass(frozen=True)
class SourceItem:
    case_id: str
    folder: Path


class MessagesRequestError(RuntimeError):
    pass


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def message_dict(message: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(message):
        return dataclasses.asdict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump()
    if hasattr(message, "__dict__"):
        return dict(vars(message))
    return {"repr": repr(message)}


def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a nonempty list")
    return [nonempty(item, f"{field}[]") for item in value]


def load_candidates(path: Path) -> list[Candidate]:
    rows: list[Candidate] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            case_id = nonempty(raw.get("case_id"), f"line {line_number}.case_id")
            if case_id in seen:
                raise ValueError(f"duplicate case_id in distillation: {case_id}")
            seen.add(case_id)
            skills = raw.get("skill_candidates")
            if not isinstance(skills, list) or not skills:
                raise ValueError(f"{case_id} has no skill_candidates")
            for index, skill in enumerate(skills):
                if not isinstance(skill, dict) or not SKILL_FIELDS.issubset(skill):
                    raise ValueError(f"invalid Skill {case_id}:{index}")
                rows.append(Candidate(
                    case_id=case_id,
                    skill_index=index,
                    skill={key: skill[key] for key in SKILL_FIELDS},
                ))
    return rows


def discover_sources(source_dir: Path, prefix: str) -> dict[str, SourceItem]:
    grouped: dict[int, list[Path]] = {}
    for folder in source_dir.iterdir():
        if not folder.is_dir():
            continue
        match = re.match(r"^(\d{4})[-_]", folder.name)
        if match:
            grouped.setdefault(int(match.group(1)), []).append(folder)
    return {
        f"{prefix}-{number:04d}": SourceItem(
            f"{prefix}-{number:04d}",
            max(folders, key=lambda path: (len(path.name), path.name)),
        )
        for number, folders in grouped.items()
    }


def select_candidates(candidates: list[Candidate], args: argparse.Namespace) -> list[Candidate]:
    case_ids = {token for token in re.split(r"[\s,]+", args.case_ids) if token}
    rows = candidates
    if case_ids:
        rows = [item for item in rows if item.case_id in case_ids]
        missing = case_ids - {item.case_id for item in rows}
        if missing:
            raise ValueError(f"unknown CASE_IDS: {sorted(missing)}")
    rows = rows[max(args.start, 0): args.end]
    if args.limit > 0:
        rows = rows[: args.limit]
    return rows


def category_assigned_keys(path: Path) -> set[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    rows = value.get("assignments") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Category assignments must contain an assignments list")
    selected: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"assignments[{index}] must be an object")
        key = nonempty(row.get("skill_key"), f"assignments[{index}].skill_key")
        category_ids = row.get("category_ids")
        if not isinstance(category_ids, list):
            raise ValueError(f"assignments[{index}].category_ids must be a list")
        if category_ids:
            selected.add(key)
    return selected


def safe_extract(archive: Path, destination: Path) -> int:
    root = destination.resolve()
    count = 0
    with zipfile.ZipFile(archive) as handle:
        for info in handle.infolist():
            if info.is_dir():
                continue
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe archive member: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            count += 1
    return count


def prepare_source(source: SourceItem, workspace: Path) -> dict[str, Any]:
    destination = workspace / "source"
    if destination.is_dir() and any(destination.iterdir()):
        return {"source_folder": str(source.folder), "reused": True}
    destination.mkdir(parents=True, exist_ok=True)
    copied, extracted = [], []
    for path in sorted(source.folder.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            target = destination / re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-_")
            target.mkdir(parents=True, exist_ok=True)
            count = safe_extract(path, target) if not any(target.iterdir()) else sum(child.is_file() for child in target.rglob("*"))
            extracted.append({"archive": path.name, "destination": target.name, "file_count": count})
        else:
            shutil.copy2(path, destination / path.name)
            copied.append(path.name)
    return {"source_folder": str(source.folder), "copied_files": copied, "extracted_archives": extracted}


def parse_frontmatter(text: str) -> tuple[str, str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md must contain YAML frontmatter")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            raise ValueError("invalid frontmatter line")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if key in values:
            raise ValueError("duplicate frontmatter key")
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        values[key] = value
    if set(values) != {"name", "description"}:
        raise ValueError("frontmatter must contain exactly name and description")
    return nonempty(values["name"], "name"), nonempty(values["description"], "description"), match.group(2)


def validate_artifact(output_dir: Path, forbidden_tokens: list[str]) -> dict[str, Any]:
    manifest_path = output_dir / "skill_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing output/skill_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"decomposition_rationale", "skills"}:
        raise ValueError("invalid skill_manifest.json keys")
    nonempty(manifest["decomposition_rationale"], "decomposition_rationale")
    skills = manifest["skills"]
    if not isinstance(skills, list) or not skills:
        raise ValueError("skill_manifest.skills must be nonempty")
    names: set[str] = set()
    normalized = []
    for index, item in enumerate(skills):
        required = {"name", "description", "relative_path", "skill_contribution", "mechanism_scope", "source_evidence"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"skills[{index}] has invalid keys")
        name = nonempty(item["name"], f"skills[{index}].name")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            raise ValueError(f"invalid Skill name: {name}")
        if name in names:
            raise ValueError(f"duplicate Skill name: {name}")
        names.add(name)
        description = nonempty(item["description"], f"skills[{index}].description")
        expected_path = f"skills/{name}/SKILL.md"
        if item["relative_path"] != expected_path:
            raise ValueError(f"relative_path must be {expected_path}")
        string_list(item["mechanism_scope"], "mechanism_scope")
        nonempty(item["skill_contribution"], "skill_contribution")
        evidence = item["source_evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("source_evidence must be nonempty")
        for row in evidence:
            if not isinstance(row, dict) or set(row) != {"path", "evidence"}:
                raise ValueError("invalid source_evidence row")
            nonempty(row["path"], "source_evidence.path")
            nonempty(row["evidence"], "source_evidence.evidence")
        skill_path = output_dir / expected_path
        if not skill_path.is_file():
            raise ValueError(f"missing {expected_path}")
        text = skill_path.read_text(encoding="utf-8")
        front_name, front_description, body = parse_frontmatter(text)
        if front_name != name or front_description != description:
            raise ValueError(f"frontmatter/manifest mismatch for {name}")
        if len(text.splitlines()) > 500:
            raise ValueError(f"{name} exceeds 500 lines")
        if not re.search(r"(?m)^# [^#\n]+$", body):
            raise ValueError(f"{name} is missing one H1")
        positions = [body.find(section) for section in REQUIRED_SECTIONS]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            raise ValueError(f"{name} is missing or reordering required sections")
        if FORBIDDEN_APPLICATION_TERMS.search(text):
            raise ValueError(f"{name} contains a prohibited application-framework term")
        lowered = text.lower()
        for token in forbidden_tokens:
            if token and token.lower() in lowered:
                raise ValueError(f"{name} leaks source identity token: {token}")
        normalized.append({**item, "skill_path": str(skill_path)})
    return {"decomposition_rationale": manifest["decomposition_rationale"], "skills": normalized}


async def call_sdk_once(candidate: Candidate, workspace: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    env = {
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": args.api_key,
        "ANTHROPIC_BASE_URL": args.base_url,
        "ANTHROPIC_MODEL": args.model,
        "CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    home = Path(args.claude_home_dir) / candidate.key
    config = Path(args.claude_config_dir) / candidate.key
    cli_home = Path(args.cli_home_dir)
    for path in (home, config, cli_home):
        path.mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(cli_home),
        "CLAUDE_HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CONFIG_HOME": str(config),
    })
    prompt = PROMPT.format(cwd=workspace)
    (workspace / "generate_skills_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    options = ClaudeAgentOptions(
        max_turns=args.max_turns,
        cli_path=args.cli_path,
        setting_sources=["project"],
        env=env,
        permission_mode=args.permission_mode,
        cwd=str(workspace),
        session_id=str(uuid.uuid4()),
        model=args.model,
    )
    trajectory, final_text = [], ""
    try:
        async for message in query(prompt=prompt, options=options):
            row = message_dict(message)
            trajectory.append(row)
            if args.print_messages:
                preview = json.dumps(row, ensure_ascii=False, default=str)[:args.message_preview_chars]
                print(f"[source-skill-msg] {candidate.key} type={message.__class__.__name__} preview={preview}", flush=True)
            result = getattr(message, "result", None)
            if isinstance(result, str) and result.strip():
                final_text = result.strip()
            if bool(getattr(message, "is_error", False)):
                raise MessagesRequestError(
                    f"http_status={getattr(message, 'api_error_status', None)!r}, "
                    f"errors={getattr(message, 'errors', None)!r}, result={final_text!r}"
                )
    except asyncio.CancelledError:
        raise
    except MessagesRequestError:
        raise
    except Exception as exc:
        raise MessagesRequestError(f"SDK messages stream error: {type(exc).__name__}: {exc}") from exc
    return trajectory, final_text


async def call_sdk(candidate: Candidate, workspace: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    attempt = 0
    retry_path = workspace / "request_retries.jsonl"
    while True:
        attempt += 1
        try:
            return await call_sdk_once(candidate, workspace, args)
        except asyncio.CancelledError:
            raise
        except MessagesRequestError as exc:
            with retry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"attempt": attempt, "error": str(exc)}, ensure_ascii=False) + "\n")
            tqdm.write(f"[source-skill-request-retry] {candidate.key} attempt={attempt} error={exc}; retrying")
            await asyncio.sleep(args.retry_sleep)


def existing_result(record_dir: Path, forbidden_tokens: list[str]) -> Optional[dict[str, Any]]:
    result_path = record_dir / "result.json"
    artifact = record_dir / "artifact"
    if not result_path.is_file() or not artifact.is_dir():
        return None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        manifest = validate_artifact(artifact, forbidden_tokens)
    except Exception:
        return None
    if result.get("status") not in {"success", "skipped_existing"}:
        return None
    return {**result, "status": "skipped_existing", "manifest": manifest}


async def process_one(
    candidate: Candidate,
    source: SourceItem,
    root: Path,
    semaphore: asyncio.Semaphore,
    args: argparse.Namespace,
) -> dict[str, Any]:
    record_dir = root / "records" / candidate.case_id / f"skill-{candidate.skill_index:02d}"
    forbidden_tokens = [candidate.case_id, source.folder.name]
    if args.resume:
        old = existing_result(record_dir, forbidden_tokens)
        if old is not None:
            return old
    workspace = record_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    write_json(record_dir / "skill.json", candidate.skill)
    write_json(workspace / "skill.json", candidate.skill)
    preparation = await asyncio.to_thread(prepare_source, source, workspace)
    write_json(workspace / "source_metadata.json", preparation)
    generation_attempt = 0
    async with semaphore:
        while True:
            generation_attempt += 1
            output_dir = workspace / "output"
            if output_dir.exists():
                shutil.rmtree(output_dir)
            output_dir.mkdir(parents=True)
            trajectory, final_text = await call_sdk(candidate, workspace, args)
            attempt_dir = record_dir / "generation_attempts" / f"attempt-{generation_attempt:04d}"
            write_jsonl(attempt_dir / "trajectory.jsonl", trajectory)
            try:
                manifest = validate_artifact(output_dir, forbidden_tokens)
            except Exception as exc:
                write_json(attempt_dir / "result.json", {
                    "status": "artifact_invalid",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "final_text": final_text,
                })
                tqdm.write(
                    f"[source-skill-artifact-retry] {candidate.key} attempt={generation_attempt} "
                    f"error={type(exc).__name__}: {exc}; retrying"
                )
                await asyncio.sleep(args.retry_sleep)
                continue
            write_json(attempt_dir / "result.json", {"status": "accepted", "error": "", "final_text": final_text})
            artifact = record_dir / "artifact"
            if artifact.exists():
                shutil.rmtree(artifact)
            shutil.copytree(output_dir, artifact)
            final_manifest = validate_artifact(artifact, forbidden_tokens)
            result = {
                "candidate_key": candidate.key,
                "case_id": candidate.case_id,
                "skill_index": candidate.skill_index,
                "status": "success",
                "error": "",
                "artifact_dir": str(artifact),
                "skill_count": len(final_manifest["skills"]),
                "manifest": final_manifest,
            }
            write_json(record_dir / "result.json", {key: value for key, value in result.items() if key != "manifest"})
            return result


async def run(args: argparse.Namespace) -> int:
    distillation_path = Path(args.distillations_jsonl).resolve()
    source_dir = Path(args.source_dir).resolve()
    root = Path(args.output_dir).resolve()
    if not distillation_path.is_file():
        raise FileNotFoundError(f"Distillation dataset missing: {distillation_path}")
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory missing: {source_dir}")
    all_candidates = load_candidates(distillation_path)
    rejected_by_category = 0
    if args.category_assignments_json:
        assignments_path = Path(args.category_assignments_json).resolve()
        if not assignments_path.is_file():
            raise FileNotFoundError(f"Category assignments missing: {assignments_path}")
        assigned = category_assigned_keys(assignments_path)
        before = len(all_candidates)
        all_candidates = [item for item in all_candidates if f"{item.case_id}:{item.skill_index}" in assigned]
        rejected_by_category = before - len(all_candidates)
    candidates = select_candidates(all_candidates, args)
    sources = discover_sources(source_dir, args.data_prefix)
    missing_sources = sorted({item.case_id for item in candidates} - set(sources))
    if missing_sources:
        raise ValueError(f"missing source folders: {missing_sources}")
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "run_config.json", {**vars(args), "api_key": "<redacted>" if args.api_key else ""})
    (root / "selected_candidates.txt").write_text("".join(item.key + "\n" for item in candidates), encoding="utf-8")

    completed, pending = [], []
    for candidate in tqdm(candidates, desc="source-skill resume scan", unit="skill"):
        record_dir = root / "records" / candidate.case_id / f"skill-{candidate.skill_index:02d}"
        old = existing_result(record_dir, [candidate.case_id, sources[candidate.case_id].folder.name]) if args.resume else None
        if old is None:
            pending.append(candidate)
        else:
            completed.append(old)
    print(f"[source-skill-resume-scan] selected={len(candidates)} completed={len(completed)} pending={len(pending)}")

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [asyncio.create_task(process_one(item, sources[item.case_id], root, semaphore, args)) for item in pending]
    results = list(completed)
    progress = tqdm(total=len(tasks), desc="source-grounded Skill pending", unit="skill")
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        progress.update(1)
        progress.set_postfix(skills=sum(item.get("skill_count", 0) for item in results))
    progress.close()

    order = {item.key: index for index, item in enumerate(candidates)}
    results.sort(key=lambda item: order[item["candidate_key"]])
    manifest_rows, skill_rows = [], []
    for result in results:
        manifest_rows.append({key: result.get(key) for key in ("candidate_key", "case_id", "skill_index", "status", "error", "artifact_dir", "skill_count")})
        for skill in result["manifest"]["skills"]:
            skill_rows.append({
                "skill_id": f"{result['candidate_key']}__{skill['name']}",
                "name": skill["name"],
                "description": skill["description"],
                "skill_path": skill["skill_path"],
                "case_id": result["case_id"],
                "skill_index": result["skill_index"],
                "skill_contribution": skill["skill_contribution"],
                "mechanism_scope": skill["mechanism_scope"],
                "source_evidence": skill["source_evidence"],
            })
    write_jsonl(root / "manifest.jsonl", manifest_rows)
    write_jsonl(root / "skill_index.jsonl", skill_rows)
    summary = {
        "category_rejected_skills": rejected_by_category,
        "selected_skills": len(candidates),
        "completed_skills": len(results),
        "generated_skills": len(skill_rows),
        "output_dir": str(root),
        "skill_index": str(root / "skill_index.jsonl"),
    }
    write_json(root / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Generate source-grounded Skills for selected Skill candidates.")
    value.add_argument("--distillations-jsonl", required=True)
    value.add_argument("--source-dir", required=True)
    value.add_argument("--output-dir", required=True)
    value.add_argument("--data-prefix", default="codrops")
    value.add_argument("--start", type=int, default=int(os.environ.get("START", "0")))
    value.add_argument("--end", type=int, default=int(os.environ["END"]) if os.environ.get("END") else None)
    value.add_argument("--limit", type=int, default=int(os.environ.get("LIMIT", "0")))
    value.add_argument("--case-ids", default=os.environ.get("CASE_IDS", ""))
    value.add_argument("--category-assignments-json", default=os.environ.get("CATEGORY_ASSIGNMENTS_JSON", ""))
    value.add_argument("--concurrency", type=int, default=int(os.environ.get("SOURCE_SKILL_WORKERS", "20")))
    value.add_argument("--resume", action=argparse.BooleanOptionalAction, default=env_bool("RESUME", True))
    value.add_argument("--model", default=os.environ.get("SKILL_MODEL_NAME", os.environ.get("MODEL", "")))
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("SKILL_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--max-turns", type=int, default=int(os.environ.get("SKILL_MAX_TURNS", "100")))
    value.add_argument("--retry-sleep", type=float, default=float(os.environ.get("SKILL_RETRY_SLEEP", "5")))
    value.add_argument("--permission-mode", default=os.environ.get("SKILL_PERMISSION_MODE", "acceptEdits"), choices=["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"])
    value.add_argument("--print-messages", action=argparse.BooleanOptionalAction, default=env_bool("SKILL_PRINT_MESSAGES", True))
    value.add_argument("--message-preview-chars", type=int, default=int(os.environ.get("SKILL_MESSAGE_PREVIEW_CHARS", "200")))
    value.add_argument("--cli-path", default=os.environ.get("CLAUDE_CLI_PATH", ""))
    value.add_argument("--claude-home-dir", required=True)
    value.add_argument("--claude-config-dir", required=True)
    value.add_argument("--cli-home-dir", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    if not args.model:
        raise ValueError("--model, SKILL_MODEL_NAME, or MODEL is required")
    if not args.api_key:
        raise ValueError("SKILL_API_KEY or API_KEY is required")
    if args.concurrency < 1:
        raise ValueError("SOURCE_SKILL_WORKERS must be positive")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
