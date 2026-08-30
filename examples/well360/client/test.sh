#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
BASE="${OSDU_BASE_URL:-http://localhost:8089}"

echo "== OSDU Well 360 health =="
curl -fsS "$BASE/health" | python3 -m json.tool

echo
echo "== real os-core-common:3.1.0 Well 360 flow =="
./run.sh
