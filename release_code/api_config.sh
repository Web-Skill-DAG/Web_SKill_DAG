#!/usr/bin/env bash
# Fill only the values you need, or export them before running a command.
# Keep all values empty in the public repository; never commit real credentials.

BASE_URL="${BASE_URL:-}"
API_KEY="${API_KEY:-}"
MODEL="${MODEL:-}"
EMBEDDING_ENCODER="${EMBEDDING_ENCODER:-}"
EMBEDDING_REVISION="${EMBEDDING_REVISION:-}"

# Optional workflow-specific keys. Leave blank to use API_KEY as the fallback.
QUERY_API_KEY="${QUERY_API_KEY:-}"
SKILL_API_KEY="${SKILL_API_KEY:-}"
MODEL_INFERENCE_API_KEY="${MODEL_INFERENCE_API_KEY:-}"

# Optional local executable paths.
CLAUDE_CLI_PATH="${CLAUDE_CLI_PATH:-}"
PYTHON="${PYTHON:-python3}"
