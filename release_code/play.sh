#!/usr/bin/env bash
# Expand one website request with the released evolved Skill DAG.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "$HERE/.." && pwd)"
CONFIG_FILE="${WEB_SKILL_DAG_CONFIG:-$HERE/api_config.sh}"
source "$CONFIG_FILE"

PYTHON="${PYTHON:-python3}"
QUERY=""

usage() {
  echo "Usage: $0 --query \"website request\"" >&2
}

while (($#)); do
  case "$1" in
    --query) QUERY=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n "$QUERY" ]] || { usage; exit 2; }
[[ -n "${BASE_URL:-}" ]] || { echo "BASE_URL is required" >&2; exit 2; }
[[ -n "${API_KEY:-}" ]] || { echo "API_KEY is required" >&2; exit 2; }
[[ -n "${MODEL:-}" ]] || { echo "MODEL is required" >&2; exit 2; }

PLAY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/webvise-play.XXXXXX")"
trap 'rm -rf -- "$PLAY_DIR"' EXIT
INPUT_JSONL="$PLAY_DIR/input.jsonl"
OUTPUT_DIR="$PLAY_DIR/output"

"$PYTHON" - "$INPUT_JSONL" "$QUERY" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
query = sys.argv[2]
path.write_text(
    json.dumps({"data_id": "play-now", "query": query}, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY

bash "$HERE/03_plug_and_play_website_query_production/run.sh" \
  --checkpoint "$REPOSITORY_ROOT/final_evolved_skill_dag" \
  --input-jsonl "$INPUT_JSONL" \
  --output-dir "$OUTPUT_DIR" \
  --limit 1 >&2

"$PYTHON" - "$OUTPUT_DIR/expanded_queries.jsonl" <<'PY'
import json
import sys
from pathlib import Path

line = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
print(json.loads(line)["query"])
PY
