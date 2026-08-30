#!/usr/bin/env bash
# Client contract check - no Maven registry required.
#
# The os-core-common 3.1.0 artifact is published only on the OSDU community
# GitLab package registry (community.opengroup.org), which is not on Maven
# Central and has been flaky during the OSDU->OpenDES hosting migration.
# CI therefore must not depend on downloading it. This check keeps the client
# buildable against a known release by pinning the version and guarding the
# exact os-core-common API surface the Well 360 demo consumes.
set -euo pipefail
cd "$(dirname "$0")"

POM="pom.xml"
APP="src/main/java/com/beyond/osdu/Well360App.java"

# 1. Exact release pin - the client compiles against one os-core-common version.
grep -q '<osdu.version>3.1.0</osdu.version>' "$POM"

# 2. No snapshots or floating versions anywhere in the pom.
if grep -Eq '<version>[^<]*(SNAPSHOT|LATEST|RELEASE)</version>' "$POM"; then
  echo "pom.xml: floating dependency versions are not allowed" >&2
  exit 1
fi

# 3. The demo imports only the documented 3.1.0 API surface, nothing else.
EXPECTED=(
  org.opengroup.osdu.core.common.http.json.HttpResponseBodyMapper
  org.opengroup.osdu.core.common.model.http.DpsHeaders
  org.opengroup.osdu.core.common.model.search.CursorQueryRequest
  org.opengroup.osdu.core.common.model.search.CursorQueryResponse
  org.opengroup.osdu.core.common.model.search.QueryRequest
  org.opengroup.osdu.core.common.model.search.QueryResponse
  org.opengroup.osdu.core.common.search.ISearchService
  org.opengroup.osdu.core.common.search.SearchAPIConfig
  org.opengroup.osdu.core.common.search.SearchFactory
  org.opengroup.osdu.core.common.storage.IStorageService
  org.opengroup.osdu.core.common.storage.StorageAPIConfig
  org.opengroup.osdu.core.common.storage.StorageFactory
)

ACTUAL=$(grep -oE 'import org\.opengroup\.osdu\.[A-Za-z0-9_.]+;' "$APP" | sed 's/^import //; s/;$//' | sort)
WANTED=$(printf '%s\n' "${EXPECTED[@]}" | sort)
if ! diff <(printf '%s\n' "$WANTED") <(printf '%s\n' "$ACTUAL") >/dev/null; then
  echo "os-core-common imports drifted from the pinned 3.1.0 surface:" >&2
  diff <(printf '%s\n' "$WANTED") <(printf '%s\n' "$ACTUAL") >&2 || true
  exit 1
fi

echo "Client contract OK: os-core-common 3.1.0, $(printf '%s\n' "${EXPECTED[@]}" | wc -l | tr -d ' ') symbols pinned"