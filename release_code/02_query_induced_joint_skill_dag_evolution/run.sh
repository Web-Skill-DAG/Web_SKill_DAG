#!/usr/bin/env bash
# Initial Visual Skill Library -> Initial Skill DAG -> representative queries -> evolved Skill DAG.
# One full query-induced evolution round. Use a fresh output directory per experiment.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${WEB_SKILL_DAG_CONFIG:-$HERE/../api_config.sh}"
source "$CONFIG_FILE"
CORE="$HERE"
PYTHON="${PYTHON:-python3}"
LIBRARY=""
OUTPUT_DIR=""
BASE_URL="${BASE_URL:-}"
QUERY_API_KEY="${QUERY_API_KEY:-${API_KEY:-}}"
EMBEDDING_ENCODER="${EMBEDDING_ENCODER:-}"
EMBEDDING_DEVICE="${EMBEDDING_DEVICE:-cuda:0}"
POTENTIAL_QUERIES=""
QUERY_LIMIT=0
CONCURRENCY=8
TOP_K=10
REVIEW_BATCH_SIZE=8
STOP_AFTER=final

usage() {
  echo "Usage: $0 --library FILE --output-dir DIR [--base-url URL] [--embedding-encoder PATH] [--embedding-device DEVICE] [--potential-queries FILE] [--query-limit N] [--concurrency N] [--top-k N] [--review-batch-size N] [--stop-after c0|queries|retrieval|diagnosis|structural|completion|final]" >&2
}
stage() {
  local label=$1 script=$2; shift 2
  echo "[$label] $script"
  "$PYTHON" "$CORE/$script" "$@"
}
done_at() { [[ "$STOP_AFTER" == "$1" ]]; }

while (($#)); do
  case "$1" in
    --library) LIBRARY=$2; shift 2 ;;
    --output-dir) OUTPUT_DIR=$2; shift 2 ;;
    --base-url) BASE_URL=$2; shift 2 ;;
    --embedding-encoder) EMBEDDING_ENCODER=$2; shift 2 ;;
    --embedding-device) EMBEDDING_DEVICE=$2; shift 2 ;;
    --potential-queries) POTENTIAL_QUERIES=$2; shift 2 ;;
    --query-limit) QUERY_LIMIT=$2; shift 2 ;;
    --concurrency) CONCURRENCY=$2; shift 2 ;;
    --top-k) TOP_K=$2; shift 2 ;;
    --review-batch-size) REVIEW_BATCH_SIZE=$2; shift 2 ;;
    --stop-after) STOP_AFTER=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

export QUERY_API_KEY

C0="$OUTPUT_DIR/c0"
POTENTIAL="$OUTPUT_DIR/potential_queries"
PQR="$OUTPUT_DIR/pqr"
SELECTED="$OUTPUT_DIR/representative_potential_queries"
RETRIEVAL="$OUTPUT_DIR/retrieval"
DIAGNOSIS="$OUTPUT_DIR/diagnosis"
STRUCTURAL="$OUTPUT_DIR/structural"
COMPLETION="$OUTPUT_DIR/completion"
FINAL="$OUTPUT_DIR/final"
QUERY_FILE="${POTENTIAL_QUERIES:-$SELECTED/representative_potential_queries.jsonl}"
EVIDENCE="$DIAGNOSIS/diagnosis_results.jsonl"

API_ARGS=(--base-url "$BASE_URL"
  --concurrency "$CONCURRENCY" --request-concurrency "$CONCURRENCY")
EDIT_ARGS=(--min-support 2 --max-relations-per-parent 160
  --split-min-relations 8 --split-min-skills 12 --split-move-fraction 0.6
  --max-splits-per-round 0 --compact-evidence-prompt
  --prompt-max-relations 160 --no-taxonomy-names-only)

# Serially integrate each Visual Skill into the evolving root Category index.
# This stage is intentionally sequential because every decision consumes the
# Category state produced by all preceding Skills.
stage c0 01_construct_initial_skill_dag.py \
  --library "$LIBRARY" --output-dir "$C0" \
  --base-url "$BASE_URL" --request-concurrency 1 --resume
done_at c0 && exit 0

if [[ -z "$POTENTIAL_QUERIES" ]]; then
  # Potential Query generation is resumable: 50 per Skill. GMM clustering and
  # medoid extraction transforms them into a fixed representative Potential-Query set.
  if [[ ! -f "$POTENTIAL/potential_queries.jsonl" ]]; then
    stage queries 02_generate_skill_conditioned_potential_queries.py \
      --skill-library "$LIBRARY" --output-dir "$POTENTIAL" \
      "${API_ARGS[@]}" --queries-per-skill 50 --resume
  else
    echo '[queries] Reusing completed potential_queries.jsonl'
  fi
  if [[ ! -f "$PQR/gmm_mean_offsets.npy" ]]; then
    stage queries 03_cluster_potential_queries.py \
      --potential-queries "$POTENTIAL/potential_queries.jsonl" \
      --output-dir "$PQR" --embedding-encoder "$EMBEDDING_ENCODER" \
      --device "$EMBEDDING_DEVICE"
  else
    echo '[queries] Reusing completed PQR embeddings/GMM'
  fi
  if [[ ! -f "$QUERY_FILE" ]]; then
    stage queries 04_select_representative_queries.py \
      --potential-queries "$POTENTIAL/potential_queries.jsonl" \
      --raw-embeddings "$PQR/raw_potential_query_embeddings.npy" \
      --gmm-means "$PQR/gmm_normalized_means.npy" \
      --gmm-offsets "$PQR/gmm_mean_offsets.npy" \
      --output-dir "$SELECTED"
  else
    echo "[queries] Reusing $QUERY_FILE"
  fi
else
  echo "[queries] Using representative Potential-Query set: $QUERY_FILE"
fi
done_at queries && exit 0

# The same representative Potential Queries route through C0, are diagnosed, then provide
# evidence for both structural partitioning and non-structural Skill edits.
stage retrieval 05_multi_path_route_skills.py \
  --checkpoint "$C0" --queries "$QUERY_FILE" --output-dir "$RETRIEVAL" \
  "${API_ARGS[@]}" --limit "$QUERY_LIMIT" \
  --top-k "$TOP_K" --review-batch-size "$REVIEW_BATCH_SIZE" \
  --resume
done_at retrieval && exit 0

stage diagnosis 06_diagnose_routing_and_applicability.py \
  --checkpoint "$C0" --retrieval-results "$RETRIEVAL/retrieval_results.jsonl" \
  --output-dir "$DIAGNOSIS" "${API_ARGS[@]}" \
  --hit-k "$TOP_K" --batch-top-k "$TOP_K" \
  --resume
done_at diagnosis && exit 0

stage structural 07_edit_skill_dag_structure.py \
  --checkpoint "$C0" --diagnosis-results "$EVIDENCE" \
  --output-dir "$STRUCTURAL" "${API_ARGS[@]}" "${EDIT_ARGS[@]}" \
  --partition-seed-skill-cap 32 --partition-only \
  --resume
done_at structural && exit 0

COMPLETED_CHECKPOINT="$COMPLETION/checkpoint"
PATCH="$STRUCTURAL/checkpoint/edit_patch.jsonl"
mapfile -t SPLIT_PARENTS < <("$PYTHON" - "$PATCH" <<'PY'
import json, sys
parents = set()
with open(sys.argv[1], encoding='utf-8') as stream:
    for line in stream:
        if line.strip():
            row = json.loads(line)
            if row.get('operation') == 'split_rewire':
                parents.add(row['remove_edge']['node'])
for parent in sorted(parents):
    print(parent)
PY
)
if ((${#SPLIT_PARENTS[@]})); then
  ONLY_PARENTS=()
  for parent in "${SPLIT_PARENTS[@]}"; do
    ONLY_PARENTS+=(--only-parent "$parent")
  done
  stage completion 08_complete_routing_partitions.py \
    --checkpoint "$STRUCTURAL/checkpoint" --output-dir "$COMPLETION" \
    --batch-size 8 "${ONLY_PARENTS[@]}" \
    "${API_ARGS[@]}" --resume
else
  echo '[completion] No new split parents; reusing structural checkpoint'
  COMPLETED_CHECKPOINT="$STRUCTURAL/checkpoint"
fi
done_at completion && exit 0

stage final 09_edit_visual_skills.py \
  --checkpoint "$COMPLETED_CHECKPOINT" --diagnosis-results "$EVIDENCE" \
  --output-dir "$FINAL" "${API_ARGS[@]}" "${EDIT_ARGS[@]}" \
  --non-structural-only --resume
echo "Final checkpoint: $FINAL/checkpoint"
