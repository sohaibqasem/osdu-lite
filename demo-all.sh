#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

cat <<'BANNER'
============================================================
OSDU Well 360 - Full POC Demo
3 Wells + 4 Wellbores + 4 WellLogs + 4 Datasets
real os-core-common:3.1.0 Search/Storage consumer
============================================================
BANNER

echo
echo "== start / rebuild OSDU Well 360 =="
"$ROOT/server/start.sh"

echo
echo "== load and run full supplied sample =="
"$ROOT/examples/well360/demo/run-full-sample.sh"

echo
echo "== final server state =="
curl -fsS http://localhost:8089/health | python3 -m json.tool

echo
cat <<'DONE'
============================================================
PASS - complete Well 360 full-sample demo finished.
Expected final state: 15 records
  Wells     : 3
  Wellbores : 4
  WellLogs  : 4
  Datasets  : 4
============================================================
DONE
