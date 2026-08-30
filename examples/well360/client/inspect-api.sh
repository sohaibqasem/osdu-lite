#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mvn -q -DskipTests dependency:copy-dependencies \
  -DincludeArtifactIds=os-core-common \
  -DoutputDirectory=target/core-common-deps

JAR="$(find target/core-common-deps -name 'os-core-common-3.1.0.jar' | head -1)"
echo "Resolved: $JAR"
echo
for cls in \
  org.opengroup.osdu.core.common.model.http.DpsHeaders \
  org.opengroup.osdu.core.common.search.SearchFactory \
  org.opengroup.osdu.core.common.search.ISearchService \
  org.opengroup.osdu.core.common.storage.StorageFactory \
  org.opengroup.osdu.core.common.storage.IStorageService
do
  echo "===== $cls ====="
  javap -classpath "$JAR" "$cls"
  echo
done
