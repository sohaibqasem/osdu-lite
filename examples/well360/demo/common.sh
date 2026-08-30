#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${OSDU_BASE_URL:-http://localhost:8089}"
PARTITION="${OSDU_PARTITION:-company-prod}"
TOKEN="${OSDU_TOKEN:-demo-token}"
AUTH=(-H "Authorization: Bearer $TOKEN" -H "data-partition-id: $PARTITION")
JSON_H=(-H "Content-Type: application/json")

osdu_get() {
  curl -fsS "${AUTH[@]}" "$@"
}

osdu_json() {
  curl -fsS "${AUTH[@]}" "${JSON_H[@]}" "$@"
}

wait_search() {
  local kind="$1"
  local query="$2"
  local label="$3"
  echo "Waiting for Search index: $label"
  for _ in {1..30}; do
    local out
    out="$(osdu_json -X POST "$BASE/api/search/v2/query" \
      --data "$(python3 - "$kind" "$query" <<'PY'
import json,sys
print(json.dumps({"kind":sys.argv[1],"query":sys.argv[2],"limit":1}))
PY
)")"
    local count
    count="$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("totalCount",0))')"
    if [ "$count" -gt 0 ]; then
      echo "Indexed: $label"
      return 0
    fi
    sleep 0.3
  done
  echo "Timed out waiting for Search: $label" >&2
  return 1
}

reset_poc() {
  echo "== reset =="
  osdu_json -X POST "$BASE/poc/reset" --data '{}' | python3 -m json.tool
}

register_demo_dataset() {
  echo "== Dataset storage instructions =="
  local instructions
  instructions="$(osdu_json -X POST "$BASE/api/dataset/v1/storageInstructions?kindSubType=dataset--File.Generic" --data '{}')"
  printf '%s\n' "$instructions" | python3 -m json.tool

  local upload_url
  upload_url="$(printf '%s' "$instructions" | python3 -c 'import json,sys; print(json.load(sys.stdin)["storageLocation"]["signedUrl"])')"

  echo "== upload synthetic LAS =="
  curl -fsS -X PUT "$upload_url" \
    -H "x-file-name: eagle01_gr_cali.las" \
    --data-binary "@$ROOT/demo/eagle01_gr_cali.las" \
    | python3 -m json.tool

  echo "== register Dataset record =="
  osdu_json -X PUT "$BASE/api/dataset/v1/registerDataset" \
    --data-binary "@$ROOT/samples/sample_dataset_record.json" \
    | python3 -m json.tool
}
