#!/usr/bin/env bash
# Evolved Skill DAG + original requests -> expanded website queries; no website generation.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${WEB_SKILL_DAG_CONFIG:-$HERE/../api_config.sh}"
source "$CONFIG_FILE"
PYTHON="${PYTHON:-python3}"
CHECKPOINT=""
INPUT_JSONL=""
OUTPUT_DIR=""
BASE_URL="${BASE_URL:-}"
MODEL="${MODEL:-}"
START=0
LIMIT=0
CONCURRENCY=8
TOP_K=10
REVIEW_BATCH_SIZE=8

usage() {
  echo "Usage: $0 --checkpoint DIR --input-jsonl FILE --output-dir DIR [--base-url URL] [--model MODEL] [--start N] [--limit N] [--concurrency N] [--top-k N] [--review-batch-size N]" >&2
}
while (($#)); do
  case "$1" in
    --checkpoint) CHECKPOINT=$2; shift 2 ;;
    --input-jsonl) INPUT_JSONL=$2; shift 2 ;;
    --output-dir) OUTPUT_DIR=$2; shift 2 ;;
    --base-url) BASE_URL=$2; shift 2 ;;
    --model) MODEL=$2; shift 2 ;;
    --start) START=$2; shift 2 ;;
    --limit) LIMIT=$2; shift 2 ;;
    --concurrency) CONCURRENCY=$2; shift 2 ;;
    --top-k) TOP_K=$2; shift 2 ;;
    --review-batch-size) REVIEW_BATCH_SIZE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

echo '[01/02] Materializing the evolved recursive DAG and full Skill documents'
"$PYTHON" "$HERE/01_materialize_checkpoint.py" \
  --checkpoint "$CHECKPOINT" --output-dir "$OUTPUT_DIR/dag_hierarchy"

export SKILL_API_KEY="${SKILL_API_KEY:-${API_KEY:-}}"
echo '[02/02] Shared recursive routing -> full-document review -> expansion -> consistency review'
"$PYTHON" "$HERE/02_expand_queries.py" \
  --input-jsonl "$INPUT_JSONL" --hierarchy-dir "$OUTPUT_DIR/dag_hierarchy" \
  --output-dir "$OUTPUT_DIR" --base-url "$BASE_URL" \
  --model "$MODEL" --model-pool "$MODEL" \
  --start "$START" --limit "$LIMIT" \
  --category-concurrency "$CONCURRENCY" \
  --expansion-concurrency "$CONCURRENCY" \
  --consistency-concurrency "$CONCURRENCY" \
  --top-k "$TOP_K" --review-batch-size "$REVIEW_BATCH_SIZE" \
  --case-concurrency "$CONCURRENCY" \
  --model-concurrency "$CONCURRENCY" \
  --model-concurrency-per-model "$CONCURRENCY" \
  --no-retry-forever
echo "Expanded queries: $OUTPUT_DIR/expanded_queries.jsonl"
