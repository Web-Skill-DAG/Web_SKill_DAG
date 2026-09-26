#!/usr/bin/env bash
# Source websites -> Goal Queries -> Experience/Interaction Descriptions -> Visual Skills.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${WEB_SKILL_DAG_CONFIG:-$HERE/../api_config.sh}"
source "$CONFIG_FILE"
CORE="$HERE"
PYTHON="${PYTHON:-python3}"
SOURCE_DIR=""
OUTPUT_DIR=""
STAGE=all
BASE_URL="${BASE_URL:-}"
MODEL="${MODEL:-}"
CLAUDE_CLI="${CLAUDE_CLI_PATH:-$(command -v claude || true)}"
LIMIT=0
QUERY_CONCURRENCY=8
DISTILL_CONCURRENCY=8
SKILL_CONCURRENCY=4

usage() {
  echo "Usage: $0 --source-dir DIR --output-dir DIR [--stage all|queries|distill|skills] [--base-url URL] [--model MODEL] [--claude-cli PATH] [--limit N] [--query-concurrency N] [--distill-concurrency N] [--skill-concurrency N]" >&2
}

while (($#)); do
  case "$1" in
    --source-dir) SOURCE_DIR=$2; shift 2 ;;
    --output-dir) OUTPUT_DIR=$2; shift 2 ;;
    --stage) STAGE=$2; shift 2 ;;
    --base-url) BASE_URL=$2; shift 2 ;;
    --model) MODEL=$2; shift 2 ;;
    --claude-cli) CLAUDE_CLI=$2; shift 2 ;;
    --limit) LIMIT=$2; shift 2 ;;
    --query-concurrency) QUERY_CONCURRENCY=$2; shift 2 ;;
    --distill-concurrency) DISTILL_CONCURRENCY=$2; shift 2 ;;
    --skill-concurrency) SKILL_CONCURRENCY=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

export QUERY_API_KEY="${QUERY_API_KEY:-${API_KEY:-}}"
export SKILL_API_KEY="${SKILL_API_KEY:-$QUERY_API_KEY}"

QUERY_FILE="$OUTPUT_DIR/queries/queries.jsonl"
DISTILL_FILE="$OUTPUT_DIR/distillation/distillation/distillations.jsonl"
if [[ "$STAGE" == all || "$STAGE" == queries ]]; then
  echo '[01/03] Synthesizing source-grounded queries with Claude Agent SDK acceptEdits'
  query_args=(--source-dir "$SOURCE_DIR" --output-dir "$OUTPUT_DIR/queries"
    --base-url "$BASE_URL" --model "$MODEL" --cli-path "$CLAUDE_CLI"
    --permission-mode acceptEdits
    --claude-home-dir "$OUTPUT_DIR/sdk/query_home"
    --claude-config-dir "$OUTPUT_DIR/sdk/query_config"
    --cli-home-dir "$OUTPUT_DIR/sdk/query_cli_home"
    --concurrency "$QUERY_CONCURRENCY" --limit "$LIMIT" --no-print-messages)
  "$PYTHON" "$CORE/01_derive_goal_queries.py" "${query_args[@]}"
fi
if [[ "$STAGE" == all || "$STAGE" == distill ]]; then
  echo '[02/03] Deriving Experience and Interaction Descriptions from observable behavior'
  "$PYTHON" "$CORE/02_synthesize_visual_skill_candidates.py" \
    --queries-jsonl "$QUERY_FILE" --output-dir "$OUTPUT_DIR/distillation" \
    --base-url "$BASE_URL" --model "$MODEL" \
    --concurrency "$DISTILL_CONCURRENCY" --no-validation-retry-forever
fi
if [[ "$STAGE" == all || "$STAGE" == skills ]]; then
  echo '[03/03] Inspecting source and constructing transferable Visual Skills'
  "$PYTHON" "$CORE/03_construct_visual_skills.py" \
    --distillations-jsonl "$DISTILL_FILE" --source-dir "$SOURCE_DIR" \
    --output-dir "$OUTPUT_DIR/skills" --base-url "$BASE_URL" --model "$MODEL" \
    --cli-path "$CLAUDE_CLI" --permission-mode acceptEdits \
    --claude-home-dir "$OUTPUT_DIR/sdk/skill_home" \
    --claude-config-dir "$OUTPUT_DIR/sdk/skill_config" \
    --cli-home-dir "$OUTPUT_DIR/sdk/skill_cli_home" \
    --concurrency "$SKILL_CONCURRENCY" --limit "$LIMIT" --no-print-messages
  echo "Skill index: $OUTPUT_DIR/skills/skill_index.jsonl"
fi
