#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

echo "== load full CSV sample =="
reset_poc
osdu_json -X POST "$BASE/poc/load-full-sample" --data '{}' | python3 -m json.tool

echo
echo "Expected source data:"
echo "  Wells     : 3"
echo "  Wellbores : 4"
echo "  WellLogs  : 4"
echo "  Datasets  : 4"

echo
echo "== run real Java OSDU client =="
(
  cd "$ROOT/client"
  ./run.sh
)

echo "PASS - full CSV sample flow completed."
