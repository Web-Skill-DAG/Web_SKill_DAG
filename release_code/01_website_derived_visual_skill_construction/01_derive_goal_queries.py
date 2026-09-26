#!/usr/bin/env python3
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
from typing import Any, Optional, Sequence

from claude_agent_sdk import ClaudeAgentOptions, query
from tqdm import tqdm

import sys as _prompt_sys
from pathlib import Path as _PromptPath

_PROMPTS_ROOT = str(_PromptPath(__file__).resolve().parents[1])
if _PROMPTS_ROOT not in _prompt_sys.path:
    _prompt_sys.path.insert(0, _PROMPTS_ROOT)

from prompts import (
    DERIVE_GOAL_QUERIES_PROMPT as PROMPT,
)



QUERY_LEVELS = ["goal", "experience", "interaction"]
SOURCE_SUFFIXES = {
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".mjs",
    ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".glsl",
    ".vert", ".frag", ".wgsl",
}
IGNORED_SOURCE_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", ".nuxt", "coverage",
    "__pycache__",
}
IGNORED_SOURCE_FILES = {
    "source_card.html", "codrops_card.html", "metadata.json", "curl_info.json",
    "headers.txt", "url.txt", "status.json",
}
SOURCE_SIGNAL = re.compile(
    r"animation|transition|transform|keyframes|scroll|wheel|pointer|mousemove|touch|drag|canvas|webgl|three|shader|clip-path|mask|blend|filter|perspective|sticky|parallax|video|marquee|carousel|gallery|hover|intersectionobserver|requestanimationframe|gsap|lenis|lottie|motion|spring|inertia",
    re.I,
)
COMPOSITION_SIGNAL = re.compile(
    r"data-(?:component|w-id|scroll|speed|hover|image|tres)|class=|<canvas|<video|<svg|position:\s*(?:fixed|sticky)|mix-blend|stroke-dash|will-change|overflow:\s*hidden|aspect-ratio|grid-template|background-image",
    re.I,
)
FORBIDDEN_TECH_PATTERNS = [
    re.compile(pattern, flags)
    for pattern, flags in [
        (r"\bAstro\b", 0),
        (r"\bBlender\b", re.IGNORECASE),
        (r"\bcanvas\b", re.IGNORECASE),
        (r"\bCSS Only\b", re.IGNORECASE),
        (r"\bFramer Motion\b", re.IGNORECASE),
        (r"\bGSAP\b", re.IGNORECASE),
        (r"\bHaxe\b", re.IGNORECASE),
        (r"\bLocomotive Scroll\b", re.IGNORECASE),
        (r"\bOGL\b", re.IGNORECASE),
        (r"\bPixi(?:\.js)?\b", re.IGNORECASE),
        (r"\bReact(?:\.js)?\b", 0),
        (r"\bReact Three Fiber\b", re.IGNORECASE),
        (r"\bSVG\b", re.IGNORECASE),
        (r"\bThree(?:\.js|js)\b", re.IGNORECASE),
        (r"\bTSL\b", re.IGNORECASE),
        (r"\bWeb Audio API\b", re.IGNORECASE),
        (r"\bWebGL\b", re.IGNORECASE),
        (r"\bWebGPU\b", re.IGNORECASE),
        (r"\bGLSL\b", re.IGNORECASE),
    ]
]
FORBIDDEN_APPLICATION_PATTERNS = [
    re.compile(pattern, flags)
    for pattern, flags in [
        (r"\bReact(?:\.js)?\b", 0),
        (r"\bReact Three Fiber\b", 0),
        (r"\bVue(?:\.js)?\b", 0),
        (r"\bSvelte(?:Kit)?\b", 0),
        (r"\bAngular\b", 0),
        (r"\bAstro\b", 0),
        (r"\bHaxe\b", 0),
        (r"\bnative DOM\b", re.IGNORECASE),
        (r"\bvanilla (?:DOM|JavaScript|JS)\b", re.IGNORECASE),
        (r"\bsingle[- ](?:file|HTML)\b", re.IGNORECASE),
        (r"\bcomponent[- ]based\b", re.IGNORECASE),
        (r"\bcomponentized\b", re.IGNORECASE),
        (r"\bSSR\b", 0),
        (r"\bCSR\b", 0),
        (r"\bSSG\b", 0),
        (r"\bJSX\b", 0),
        (r"\bTSX\b", 0),
        (r"\bVite\b", 0),
        (r"\bWebpack\b", 0),
        (r"\bParcel\b", 0),
    ]
]
@dataclasses.dataclass(frozen=True)
class Item:
    data_id: str
    number: int
    folder: Path
    fields: dict[str, Any]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_files(folder: Path) -> list[Path]:
    result: list[Path] = []
    for current, directories, names in os.walk(folder):
        directories[:] = [name for name in directories if name not in IGNORED_SOURCE_DIRS]
        base = Path(current)
        for name in names:
            path = base / name
            if (
                name.lower() not in IGNORED_SOURCE_FILES
                and path.suffix.lower() in SOURCE_SUFFIXES
                and path.stat().st_size > 0
            ):
                result.append(path)
    preferred = {"index.html", "index.htm", "main.js", "app.js", "main.ts", "app.ts"}
    return sorted(result, key=lambda path: (0 if path.name.lower() in preferred else 1, str(path)))


def compact_source_evidence(folder: Path, max_chars: int) -> dict[str, Any]:
    files = source_files(folder)
    samples: list[str] = []
    used = 0
    for path in files[:80]:
        if used >= max_chars:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(
            r"data:[^;\"']+;base64,[A-Za-z0-9+/=]{256,}",
            "[embedded-data-removed]",
            text,
        )
        if path.suffix.lower() in {".html", ".htm"}:
            text = re.sub(r">\s*<", ">\n<", text)
            text = re.sub(r"([{};])", r"\1\n", text)
        lines = text.splitlines()
        selected: list[str] = []
        for index, line in enumerate(lines):
            if SOURCE_SIGNAL.search(line) or COMPOSITION_SIGNAL.search(line):
                lo, hi = max(0, index - 2), min(len(lines), index + 3)
                selected.extend(f"{i + 1}: {lines[i][:1200]}" for i in range(lo, hi))
            if sum(len(value) for value in selected) >= 18_000:
                break
        if not selected and path.name.lower() in {"index.html", "index.htm"}:
            selected = [line[:500] for line in lines[:80]]
        if selected:
            block = f"FILE {path.relative_to(folder)}\n" + "\n".join(dict.fromkeys(selected))
            remaining = max_chars - used
            samples.append(block[:remaining])
            used += min(len(block), remaining)
    return {
        "source_file_count": len(files),
        "source_files": [str(path.relative_to(folder)) for path in files[:100]],
        "evidence_excerpt": "\n\n".join(samples),
        "evidence_truncated": used >= max_chars,
    }


def read_metadata(folder: Path) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    status_path = folder / "status.json"
    if status_path.is_file():
        try:
            raw = json.loads(status_path.read_text(encoding="utf-8", errors="replace"))
            for section in ("project", "download"):
                if isinstance(raw.get(section), dict):
                    fields.update(raw[section])
        except (OSError, json.JSONDecodeError):
            pass
    url_path = folder / "url.txt"
    if url_path.is_file():
        for line in url_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                fields[key.strip()] = value.strip()
    return fields


def prepare_readable_source(folder: Path, destination: Path) -> dict[str, Any]:
    """Create bounded, line-addressable copies of minified source text."""
    source_suffixes = {
        ".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".mjs",
        ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".json",
        ".glsl", ".vert", ".frag", ".wgsl",
    }
    ignored_dirs = {".git", "node_modules", "dist", "build", ".next", ".nuxt", "coverage", "__pycache__"}
    ignored_files = {"source_card.html", "codrops_card.html", "curl_info.json", "headers.txt", "url.txt", "status.json"}
    destination.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    total_chars = 0
    max_total_chars = 5_000_000
    for current, directories, names in os.walk(folder):
        directories[:] = [name for name in directories if name not in ignored_dirs]
        for name in sorted(names):
            path = Path(current) / name
            if name.lower() in ignored_files or path.suffix.lower() not in source_suffixes:
                continue
            if total_chars >= max_total_chars:
                break
            text = path.read_text(encoding="utf-8", errors="replace")
            text = re.sub(
                r"data:[^;\"']+;base64,[A-Za-z0-9+/=]{256,}",
                "[embedded-data-removed]",
                text,
            )
            if path.suffix.lower() in {".html", ".htm", ".vue", ".svelte", ".astro"}:
                text = re.sub(r">\s*<", ">\n<", text)
                text = re.sub(r"([{};])", r"\1\n", text)
            elif path.suffix.lower() in {".css", ".scss", ".sass", ".less", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"}:
                text = re.sub(r"([{};])", r"\1\n", text)
            remaining = max_total_chars - total_chars
            text = text[:remaining]
            relative = path.relative_to(folder)
            target = destination / relative.parent / f"{relative.name}.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            total_chars += len(text)
            written.append({"source": str(relative), "readable": str(target.relative_to(destination)), "chars": len(text)})
    return {"files": written, "total_chars": total_chars, "truncated": total_chars >= max_total_chars}


def discover(source_dir: Path, prefix: str) -> list[Item]:
    # Some downloads contain both a truncated directory name and its complete
    # counterpart for the same numeric Codrops record. Treat those as one
    # website ID and prefer the complete (longest) directory name.
    folders_by_number: dict[int, list[Path]] = {}
    for folder in sorted(path for path in source_dir.iterdir() if path.is_dir()):
        match = re.match(r"^(\d{4})[-_]", folder.name)
        if not match:
            continue
        number = int(match.group(1))
        folders_by_number.setdefault(number, []).append(folder)
    items: list[Item] = []
    for number in sorted(folders_by_number):
        folder = max(folders_by_number[number], key=lambda path: (len(path.name), path.name))
        items.append(Item(f"{prefix}-{number:04d}", number, folder, read_metadata(folder)))
    return items


def normalize_id(token: str, prefix: str) -> str:
    token = token.strip()
    if token.isdigit():
        return f"{prefix}-{int(token):04d}"
    return token


def selected_ids(args: argparse.Namespace) -> set[str]:
    result = {
        normalize_id(token, args.data_prefix)
        for token in re.split(r"[\s,]+", args.case_ids)
        if token.strip()
    }
    if args.case_ids_file:
        for line in Path(args.case_ids_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                result.add(normalize_id(line.split()[0], args.data_prefix))
    return result


def select(items: list[Item], args: argparse.Namespace) -> list[Item]:
    ids = selected_ids(args)
    if ids:
        items = [item for item in items if item.data_id in ids]
    start = max(args.start, 0)
    if args.end is not None:
        return items[start : args.end]
    if args.limit > 0:
        return items[start : start + args.limit]
    return items[start:]


def safe_extract(archive: Path, destination: Path) -> int:
    root = destination.resolve()
    count = 0
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (destination / info.filename).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"unsafe zip member: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            count += 1
    return count


def prepare_source(
    item: Item, workspace: Path, force: bool, include_directories: bool = False
) -> dict[str, Any]:
    destination = workspace / "source"
    if force and destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[dict[str, Any]] = []
    copied: list[str] = []
    copied_directories: list[dict[str, Any]] = []
    for path in sorted(item.folder.iterdir()):
        if path.is_dir():
            if include_directories:
                target = destination / path.name
                shutil.copytree(path, target, dirs_exist_ok=True)
                copied_directories.append({
                    "directory": path.name,
                    "file_count": sum(1 for child in target.rglob("*") if child.is_file()),
                })
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            target = destination / re.sub(r"[^a-zA-Z0-9._-]+", "-", path.stem).strip("-_")
            if force and target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
            if any(target.iterdir()):
                count = sum(1 for child in target.rglob("*") if child.is_file())
            else:
                count = safe_extract(path, target)
            extracted.append({"archive": path.name, "destination": target.name, "file_count": count})
        else:
            shutil.copy2(path, destination / path.name)
            copied.append(path.name)
    if not any(path.is_file() for path in destination.rglob("*")):
        raise RuntimeError("source workspace is empty")
    return {
        "copied_files": copied,
        "copied_directories": copied_directories,
        "extracted_archives": extracted,
    }


def validate_rows(value: Any, top_level_field: str) -> None:
    if not isinstance(value, dict):
        raise ValueError("top-level value must be an object")
    if set(value) != {top_level_field}:
        raise ValueError(f"the only top-level field must be {top_level_field}")
    rows = value.get(top_level_field)
    if not isinstance(rows, list) or len(rows) != len(QUERY_LEVELS):
        raise ValueError(f"{top_level_field} must contain exactly three records")
    levels = [row.get("level") for row in rows if isinstance(row, dict)]
    if levels != QUERY_LEVELS:
        raise ValueError(f"query levels must appear in order: {QUERY_LEVELS}")
    for row in rows:
        if set(row) != {"level", "text"}:
            raise ValueError(f"each {top_level_field} record must contain exactly level and text")
        if not str(row.get("text", "")).strip():
            raise ValueError(f"empty text for {row['level']}")


def validate_segments(segments: Any) -> None:
    validate_rows(segments, "segments")
    for row in segments["segments"]:
        for pattern in FORBIDDEN_APPLICATION_PATTERNS:
            match = pattern.search(row["text"])
            if match:
                raise ValueError(
                    f"prohibited application-layer implementation in {row['level']} segment: "
                    f"{match.group(0)}"
                )
        for pattern in FORBIDDEN_TECH_PATTERNS:
            match = pattern.search(row["text"])
            if match:
                raise ValueError(
                    f"prohibited technology label in {row['level']} segment: {match.group(0)}"
                )


def extract_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object in agent response")
def message_dict(message: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(message):
        return dataclasses.asdict(message)
    if hasattr(message, "model_dump"):
        return message.model_dump()
    if hasattr(message, "__dict__"):
        return dict(vars(message))
    return {"repr": repr(message)}


class MessagesRequestError(RuntimeError):
    """The Claude SDK messages stream failed or returned an API error result."""


async def call_agent_once(item: Item, workspace: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    data_id = item.data_id
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
    home = Path(args.claude_home_dir) / data_id
    config = Path(args.claude_config_dir) / data_id
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
    if args.inline_evidence:
        evidence = json.loads((workspace / "compact_source_evidence.json").read_text(encoding="utf-8"))
        metadata = json.loads((workspace / "source_metadata.json").read_text(encoding="utf-8"))
        prompt += """

SUPPLIED SOURCE EVIDENCE
Treat the structured payload below as the complete source evidence. Omit any
behavior or technology it does not establish. Return only the requested JSON
object without Markdown.

EVIDENCE:
""" + json.dumps(
            {"metadata": metadata, "source": evidence},
            ensure_ascii=False,
        )
    (workspace / "synthesize_query_prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    options = ClaudeAgentOptions(
        max_turns=args.max_turns,
        cli_path=args.cli_path,
        setting_sources=["project"],
        env=env,
        allowed_tools=[] if args.inline_evidence else ["Read", "Glob", "Grep", "Write"],
        disallowed_tools=["Bash", "Task", "WebFetch", "WebSearch"] + (
            ["Read", "Glob", "Grep", "Write", "Edit"] if args.inline_evidence else []
        ),
        permission_mode=args.permission_mode,
        cwd=str(workspace),
        session_id=str(uuid.uuid4()),
    )
    trajectory: list[dict[str, Any]] = []
    final_text = ""
    try:
        timeout = None if args.request_timeout <= 0 else args.request_timeout
        async with asyncio.timeout(timeout):
            async for message in query(prompt=prompt, options=options):
                row = message_dict(message)
                trajectory.append(row)
                if args.print_messages:
                    kind = message.__class__.__name__
                    serialized = json.dumps(row, ensure_ascii=False, default=str)
                    content = serialized if args.message_preview_chars <= 0 else serialized[: args.message_preview_chars]
                    print(f"[query-msg] {data_id} type={kind} content={content}", flush=True)
                text = getattr(message, "result", None)
                if isinstance(text, str) and text.strip():
                    final_text = text.strip()
                if bool(getattr(message, "is_error", False)):
                    status = getattr(message, "api_error_status", None)
                    errors = getattr(message, "errors", None)
                    raise MessagesRequestError(
                        f"http_status={status!r}, errors={errors!r}, result={final_text!r}"
                    )
    except asyncio.CancelledError:
        raise
    except MessagesRequestError:
        raise
    except Exception as exc:
        raise MessagesRequestError(f"SDK messages stream error: {type(exc).__name__}: {exc}") from exc
    return trajectory, final_text


async def call_agent(item: Item, workspace: Path, args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    """Retry one case's failed SDK/messages request in place until it succeeds."""
    retry_path = workspace / "request_retries.jsonl"
    attempt = 0
    while True:
        attempt += 1
        (workspace / "query_segments.json").unlink(missing_ok=True)
        try:
            return await call_agent_once(item, workspace, args)
        except asyncio.CancelledError:
            raise
        except MessagesRequestError as exc:
            retry_event = {
                "attempt": attempt,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            with retry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(retry_event, ensure_ascii=False) + "\n")
            tqdm.write(
                f"[query-request-retry] {item.data_id} attempt={attempt} "
                f"error={type(exc).__name__}: {exc}; "
                f"retrying in {args.request_retry_sleep}s"
            )
            await asyncio.sleep(args.request_retry_sleep)


def write_trajectory(path: Path, trajectory: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in trajectory:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


async def generate_segments_until_valid(
    item: Item,
    workspace: Path,
    record_dir: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repeat this case's generation until its required artifact validates."""
    generated_path = workspace / "query_segments.json"
    generation_attempt = 0
    while True:
        generation_attempt += 1
        generated_path.unlink(missing_ok=True)
        trajectory, final_text = await call_agent(item, workspace, args)
        if args.inline_evidence and not generated_path.is_file():
            write_json(generated_path, extract_json_object(final_text))
        attempt_dir = record_dir / "generation_attempts" / f"attempt-{generation_attempt:04d}"
        write_trajectory(attempt_dir / "trajectory.jsonl", trajectory)
        try:
            if not generated_path.is_file():
                raise RuntimeError("agent did not write query_segments.json")
            segments = json.loads(generated_path.read_text(encoding="utf-8"))
            validate_segments(segments)
        except (OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            write_json(attempt_dir / "result.json", {
                "status": "artifact_invalid",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "final_text": final_text,
            })
            tqdm.write(
                f"[query-artifact-retry] {item.data_id} generation_attempt={generation_attempt} "
                f"error={type(exc).__name__}: {exc}; "
                f"retrying in {args.artifact_retry_sleep}s"
            )
            await asyncio.sleep(args.artifact_retry_sleep)
            continue
        write_json(attempt_dir / "result.json", {
            "status": "accepted",
            "error": "",
            "final_text": final_text,
        })
        return segments, trajectory


def existing_result(output_dir: Path, data_id: str) -> Optional[dict[str, Any]]:
    result_path = output_dir / "records" / data_id / "result.json"
    segments_path = output_dir / "records" / data_id / "query_segments.json"
    if not result_path.is_file() or not segments_path.is_file():
        return None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        segments = json.loads(segments_path.read_text(encoding="utf-8"))
        validate_segments(segments)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if result.get("status") not in {"success", "dry_run", "skipped_existing"}:
        return None
    result["status"] = "skipped_existing"
    result.pop("record_path", None)
    result["segments_path"] = str(segments_path)
    return result


def dry_run_record(item: Item) -> dict[str, Any]:
    return {
        "segments": [
            {"level": level, "text": f"DRY_RUN {level} segment for {item.data_id}."}
            for level in QUERY_LEVELS
        ],
    }


async def process(item: Item, args: argparse.Namespace, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    if args.resume:
        old = existing_result(output_dir, item.data_id)
        if old:
            record_dir = output_dir / "records" / item.data_id
            (record_dir / "query_record.json").unlink(missing_ok=True)
            write_json(record_dir / "result.json", old)
            return old
    async with semaphore:
        record_dir = output_dir / "records" / item.data_id
        workspace = output_dir / "runtime" / item.data_id / "query_workspace"
        record_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        result_path = record_dir / "result.json"
        try:
            preparation = prepare_source(
                item, workspace, args.force_source, args.include_source_directories
            )
            metadata = {
                "website_id": item.data_id,
                "source_folder": str(item.folder),
                "source_fields": item.fields,
                "source_preparation": preparation,
            }
            write_json(workspace / "source_metadata.json", metadata)
            if not args.inline_evidence:
                readable_manifest = prepare_readable_source(
                    item.folder, workspace / "readable_source"
                )
                write_json(workspace / "readable_source_manifest.json", readable_manifest)
            # A compact, line-broken source index prevents SDK agents from
            # repeatedly fighting huge minified one-line HTML with ad-hoc
            # parsing scripts. It supplements rather than replaces ./source.
            compact_path = workspace / "compact_source_evidence.json"
            if not compact_path.is_file():
                write_json(compact_path, compact_source_evidence(item.folder, 50_000))
            if args.dry_run:
                segments = dry_run_record(item)
                trajectory: list[dict[str, Any]] = []
                status = "dry_run"
            else:
                segments, trajectory = await generate_segments_until_valid(
                    item, workspace, record_dir, args
                )
                status = "success"
            validate_segments(segments)
            write_json(record_dir / "query_segments.json", segments)
            (record_dir / "query_record.json").unlink(missing_ok=True)
            write_trajectory(record_dir / "trajectory.jsonl", trajectory)
            result = {
                "data_id": item.data_id,
                "status": status,
                "error": "",
                "segments_path": str(record_dir / "query_segments.json"),
                "workspace": str(workspace),
            }
        except Exception as exc:
            result = {
                "data_id": item.data_id,
                "status": "failed",
                "error": str(exc),
                "segments_path": str(record_dir / "query_segments.json"),
                "workspace": str(workspace),
            }
        write_json(result_path, result)
        return result


async def run(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source_dir}")
    items = select(discover(source_dir, args.data_prefix), args)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = vars(args).copy()
    config["api_key"] = "<redacted>" if args.api_key else ""
    write_json(output_dir / "run_config.json", config)
    (output_dir / "selected_ids.txt").write_text("".join(f"{item.data_id}\n" for item in items), encoding="utf-8")
    cached_results: list[dict[str, Any]] = []
    pending_items = items
    if args.resume:
        pending_items = []
        scan = tqdm(items, total=len(items), desc="resume scan", unit="website")
        for item in scan:
            cached = existing_result(output_dir, item.data_id)
            if cached is None:
                pending_items.append(item)
            else:
                cached_results.append(cached)
        tqdm.write(
            f"[resume-scan] selected={len(items)} "
            f"completed={len(cached_results)} pending={len(pending_items)}"
        )

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [process(item, args, semaphore) for item in pending_items]
    results: list[dict[str, Any]] = list(cached_results)
    progress = tqdm(
        asyncio.as_completed(tasks),
        total=len(tasks),
        desc="synthetic query pending",
        unit="website",
    )
    for future in progress:
        result = await future
        results.append(result)
        tqdm.write(f"[query] {result['data_id']} {result['status']} {result.get('error', '')}")
    results.sort(key=lambda row: row["data_id"])
    with (output_dir / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    dataset_rows: list[dict[str, Any]] = []
    for result in results:
        if result["status"] not in {"success", "dry_run", "skipped_existing"}:
            continue
        segment_record = json.loads(Path(result["segments_path"]).read_text(encoding="utf-8"))
        validate_segments(segment_record)
        dataset_rows.append({
            "website_id": result["data_id"],
            "segments": segment_record["segments"],
        })
    with (output_dir / "queries.jsonl").open("w", encoding="utf-8") as handle:
        for row in dataset_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "selected": len(items),
        "successful": sum(row["status"] in {"success", "dry_run", "skipped_existing"} for row in results),
        "failed": sum(row["status"] == "failed" for row in results),
        "sample_count": len(dataset_rows),
        "segment_count": sum(len(row["segments"]) for row in dataset_rows),
        "output_dir": str(output_dir),
    }
    write_json(output_dir / "summary.json", summary)
    return 1 if summary["failed"] else 0


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "no", "off"}


def parser() -> argparse.ArgumentParser:
    work_root = Path(__file__).resolve().parent.parent / "runtime"
    value = argparse.ArgumentParser(description="Synthesize multi-level user queries from Codrops source websites.")
    value.add_argument("--source-dir", default=os.environ.get("SOURCE_DIR", ""))
    value.add_argument("--output-dir", required=True)
    value.add_argument("--data-prefix", default="codrops")
    value.add_argument("--start", type=int, default=int(os.environ.get("START", "0")))
    value.add_argument("--end", type=int, default=int(os.environ["END"]) if os.environ.get("END") else None)
    value.add_argument("--limit", type=int, default=int(os.environ.get("LIMIT", "20")))
    value.add_argument("--case-ids", default=os.environ.get("CASE_IDS", ""))
    value.add_argument("--case-ids-file", default=os.environ.get("CASE_IDS_FILE", ""))
    value.add_argument("--concurrency", type=int, default=int(os.environ.get("QUERY_WORKERS", "10")))
    value.add_argument("--resume", action=argparse.BooleanOptionalAction, default=env_bool("RESUME", True))
    value.add_argument("--dry-run", action="store_true", default=env_bool("DRY_RUN", False))
    value.add_argument("--force-source", action="store_true", default=env_bool("FORCE_SOURCE", False))
    value.add_argument(
        "--inline-evidence",
        action=argparse.BooleanOptionalAction,
        default=env_bool("QUERY_INLINE_EVIDENCE", False),
        help="Embed precomputed source evidence and synthesize without agent tool calls.",
    )
    value.add_argument(
        "--include-source-directories",
        action=argparse.BooleanOptionalAction,
        default=env_bool("INCLUDE_SOURCE_DIRECTORIES", False),
        help="Recursively copy downloaded source subdirectories into each SDK workspace.",
    )
    value.add_argument("--cli-path", default=os.environ.get("CLAUDE_CLI_PATH", "claude"))
    value.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    value.add_argument("--api-key", default=os.environ.get("QUERY_API_KEY", os.environ.get("API_KEY", "")))
    value.add_argument("--model", default=os.environ.get("QUERY_MODEL_NAME", os.environ.get("MODEL", "")))
    value.add_argument("--max-turns", type=int, default=int(os.environ.get("QUERY_MAX_TURNS", "80")))
    value.add_argument(
        "--request-timeout",
        type=float,
        default=float(os.environ.get("QUERY_REQUEST_TIMEOUT", "1800")),
        help="Maximum seconds for one SDK case attempt; <=0 disables the outer timeout.",
    )
    value.add_argument(
        "--request-retry-sleep",
        type=float,
        default=float(os.environ.get("QUERY_REQUEST_RETRY_SLEEP", "5")),
        help="Seconds between infinite per-case SDK/messages request retries.",
    )
    value.add_argument(
        "--artifact-retry-sleep",
        type=float,
        default=float(os.environ.get("QUERY_ARTIFACT_RETRY_SLEEP", "5")),
        help="Seconds between infinite per-case missing/invalid artifact retries.",
    )
    value.add_argument(
        "--permission-mode",
        default=os.environ.get("QUERY_PERMISSION_MODE", "acceptEdits"),
        choices=["default", "acceptEdits", "bypassPermissions", "plan", "dontAsk"],
    )
    value.add_argument("--print-messages", action=argparse.BooleanOptionalAction, default=env_bool("QUERY_PRINT_MESSAGES", True))
    value.add_argument("--message-preview-chars", type=int, default=int(os.environ.get("QUERY_MESSAGE_PREVIEW_CHARS", "500")))
    value.add_argument("--claude-home-dir", default=os.environ.get("QUERY_CLAUDE_HOME_DIR", str(work_root / ".claude_home")))
    value.add_argument("--claude-config-dir", default=os.environ.get("QUERY_CLAUDE_CONFIG_DIR", str(work_root / ".claude_config")))
    value.add_argument("--cli-home-dir", default=os.environ.get("QUERY_CLI_HOME_DIR", str(work_root / "official_claude_home")))
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    if not args.dry_run and not args.model:
        print("--model, QUERY_MODEL_NAME, or MODEL is required for a non-dry-run invocation.", file=sys.stderr)
        return 2
    if not args.dry_run and not args.api_key:
        print("QUERY_API_KEY or API_KEY is required for a non-dry-run invocation.", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
