#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

echo
echo "============================================================"
echo "Tutorial-aligned Well 360 POC"
echo "Well -> Wellbore -> Dataset/LAS -> WellLog -> Search/Storage"
echo "============================================================"

reset_poc

echo
echo "== 1. Entitlements =="
osdu_get "$BASE/api/entitlements/v1/groups?roleRequired=true" | python3 -m json.tool

echo
echo "== 2. LegalTag =="
osdu_get "$BASE/api/legal/v1/legaltags" | python3 -m json.tool

echo
echo "== 3. Confirm Well schema =="
osdu_get "$BASE/api/schema-service/v1/schema/osdu:wks:master-data--Well:1.4.0" | python3 -m json.tool

echo
echo "== 4. Kinds =="
osdu_get "$BASE/api/storage/v2/query/kinds?limit=100" | python3 -m json.tool

echo
echo "== 5. Direct Storage ingestion: EAGLE-01 Well =="
osdu_json -X PUT "$BASE/api/storage/v2/records" \
  --data-binary "@$ROOT/samples/sample_well_record.json" | python3 -m json.tool

echo
echo "== 6. Direct Storage ingestion: EAGLE-01-A Wellbore =="
osdu_json -X PUT "$BASE/api/storage/v2/records" \
  --data-binary "@$ROOT/samples/sample_wellbore_record.json" | python3 -m json.tool

echo
register_demo_dataset

echo
echo "== 7. Direct Storage ingestion: WellLog =="
osdu_json -X PUT "$BASE/api/storage/v2/records" \
  --data-binary "@$ROOT/samples/sample_welllog_record.json" | python3 -m json.tool

echo
wait_search "osdu:wks:master-data--Well:*" 'data.FacilityName:"EAGLE-01"' "EAGLE-01"
wait_search "osdu:wks:master-data--Wellbore:*" "data.WellID:\"$PARTITION:master-data--Well:W-1001\"" "EAGLE-01-A"
wait_search "osdu:wks:work-product-component--WellLog:*" "data.WellboreID:\"$PARTITION:master-data--Wellbore:WB-1001-A\"" "LOG-1001-GR"

echo
echo "== 8. Run the real os-core-common:3.1.0 Java consumer =="
(
  cd "$ROOT/client"
  ./run.sh
)

echo
echo "PASS - tutorial-aligned flow completed."
