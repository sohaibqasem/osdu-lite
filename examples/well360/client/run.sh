#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export OSDU_BASE_URL="${OSDU_BASE_URL:-http://localhost:8089}"
export OSDU_PARTITION="${OSDU_PARTITION:-company-prod}"
export OSDU_TOKEN="${OSDU_TOKEN:-demo-token}"

mvn -q -DskipTests compile exec:java \
  -Dexec.mainClass=com.beyond.osdu.Well360App
