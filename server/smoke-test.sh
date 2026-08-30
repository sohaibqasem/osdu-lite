#!/usr/bin/env bash
set -euo pipefail
BASE="${OSDU_BASE_URL:-http://localhost:8089}"
PARTITION="${OSDU_PARTITION:-company-prod}"
TOKEN="${OSDU_TOKEN:-demo-token}"
H=(-H "Authorization: Bearer $TOKEN" -H "data-partition-id: $PARTITION" -H "Content-Type: application/json")

HEALTH_FILE="$(mktemp)"
trap 'rm -f "$HEALTH_FILE"' EXIT

curl -fsS "$BASE/health" > "$HEALTH_FILE"

echo "== health =="
python3 -m json.tool < "$HEALTH_FILE"

python3 - "$HEALTH_FILE" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    h = json.load(f)
assert h["status"] == "UP", h
assert h["partition"] == "company-prod", h
profile = h.get("currentDataProfile")
if h.get("preloadFullSampleConfigured") and not h.get("preloadedFullSample"):
    print()
    print(f"NOTE: full-sample preload is configured, but current state is '{profile}'.")
    print("      A demo reset/reloaded the in-memory state. This is expected.")
    print("      Restore/run the full state with: ../examples/well360/demo/run-full-sample.sh")
PY

echo "== entitlements =="
curl -fsS "${H[@]}" "$BASE/api/entitlements/v1/groups?roleRequired=true" | python3 -m json.tool

echo "== legal tags =="
curl -fsS "${H[@]}" "$BASE/api/legal/v1/legaltags" | python3 -m json.tool

echo "== kinds =="
curl -fsS "${H[@]}" "$BASE/api/storage/v2/query/kinds?limit=100" | python3 -m json.tool

echo "== EAGLE-01 search =="
curl -fsS "${H[@]}" -X POST "$BASE/api/search/v2/query" \
  --data '{"kind":"osdu:wks:master-data--Well:*","query":"data.FacilityName:\"EAGLE-01\"","returnedFields":["id","kind","data.FacilityName","tags"],"limit":20}' \
  | python3 -m json.tool

echo "PASS"
