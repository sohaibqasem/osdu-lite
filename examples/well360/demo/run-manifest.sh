#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

echo
echo "============================================================"
echo "Manifest/Workflow POC"
echo "Uses the supplied manifest structure with the demo LegalTag."
echo "============================================================"

reset_poc
register_demo_dataset

echo
echo "== submit Osdu_ingest manifest =="
osdu_json -X POST "$BASE/api/workflow/v1/workflow/Osdu_ingest/workflowRun" \
  --data-binary "@$ROOT/samples/manifest_example.json" | python3 -m json.tool

echo
wait_search "osdu:wks:master-data--Well:*" 'data.FacilityName:"EAGLE-01"' "EAGLE-01 from manifest"
wait_search "osdu:wks:master-data--Wellbore:*" "data.WellID:\"$PARTITION:master-data--Well:W-1001\"" "Wellbore from manifest"
wait_search "osdu:wks:work-product-component--WellLog:*" "data.WellboreID:\"$PARTITION:master-data--Wellbore:WB-1001-A\"" "WellLog from manifest"

echo
echo "== run real Java OSDU client =="
(
  cd "$ROOT/client"
  ./run.sh
)

echo "PASS - manifest flow completed."
