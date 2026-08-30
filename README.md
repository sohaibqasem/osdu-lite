# OSDU-Lite

An in-memory emulator for the OSDU service APIs, for development, demos, and CI.

[![CI](https://github.com/sohaibqasem/osdu-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/sohaibqasem/osdu-lite/actions/workflows/ci.yml)
[![Container image](https://github.com/sohaibqasem/osdu-lite/actions/workflows/publish-docker.yml/badge.svg)](https://github.com/sohaibqasem/osdu-lite/actions/workflows/publish-docker.yml)

A single-file Python HTTP server that serves the same contracts as Storage,
Search, Dataset, File, Workflow, Legal, Schema, and Entitlements. An OSDU client
built against a real deployment points at `http://localhost:8089` instead and
runs unchanged. Think LocalStack for OSDU: no cloud, no OpenSearch, no OIDC.

This repo also contains a working integration around it - the Well 360 example
(Well -> Wellbore -> WellLog -> Dataset -> LAS) with a Java consumer compiled
against `os-core-common`. See [Releases and changelog](CHANGELOG.md).

> Scope: this is a test double, not a deployment. It proves workflows and payload
> shapes; it does not implement OpenSearch semantics, data-plane ACLs at scale,
> legal blocking, or OIDC/IAM.

## Quickstart

### Pull the image (no clone, no build)

```bash
docker run --rm -d --name osdu-well360 -p 8089:8089 ghcr.io/sohaibqasem/osdu-lite
```

`latest` tracks `main`; every `v*` tag also publishes its own image, e.g.
`ghcr.io/sohaibqasem/osdu-lite:v0.7.0-beta.1`. Both `amd64` and `arm64`.

### From source

Prereqs: Python 3, Docker + Compose (for the container path only). Java 17+ and
Maven are needed for the demo client.

```bash
make start        # build and start the emulator on :8089, 15 Well 360 records preloaded
make smoke        # health / entitlements / legal / kinds / search checks
make java         # reference client (real os-core-common) against the emulator
make stop
```

`make demo` does all four in sequence. `make pull-up` is the image equivalent of
`make start` (via `server/compose.pull.yaml`).

### Runtime defaults

| Env var              | Default              | Meaning                                       |
| -------------------- | -------------------- | --------------------------------------------- |
| `PORT`               | `8089`               | listen port                                   |
| `OSDU_PARTITION`     | `company-prod`       | accepted `data-partition-id`                  |
| `OSDU_TOKEN`         | `demo-token`         | accepted bearer token                         |
| `REQUIRE_AUTH`       | `true`               | validate `Authorization` and RBAC             |
| `PRELOAD_FULL_SAMPLE`| `true`               | seed 15 Well 360 records on start             |
| `INDEX_DELAY_SECONDS`| `0.7`                | simulated indexer delay before Search sees a record |

The demo token is a member of every group, so RBAC checks (HTTP 403 on a missing
entitlement) pass in all walkthroughs but still fail for unknown tokens.

## What is emulated

All in memory; every process restart returns to a clean state.

| Service      | API  | Highlights                                                          |
| ------------ | ---- | ------------------------------------------------------------------- |
| Storage      | v2   | PUT/GET/PATCH/DELETE, version history, copy, batch ops, kinds, cursors |
| Search       | v2   | Lucene-style queries, cursor queries, aggregations, as-owner, health checks |
| Dataset      | v1   | registration, storage/retrieval instructions, registry, revoke, soft delete |
| File         | v2   | metadata, upload URLs, signed delivery, getLocation                  |
| Workflow     | v1   | deploy/list/run lifecycle, `Osdu_ingest` manifest ingestion          |
| Legal        | v1   | tag CRUD, validate, batch retrieve, properties, query operators      |
| Schema       | v1   | kind list, schema create/update/system                               |
| Entitlements | v1/v2 | groups, members, membersCount, roles, appIds                       |

Two behaviors matter for anyone writing code against it:

- **Records are validated.** An ingest requires a supported `kind`, a populated
  `acl` (`viewers`/`owners`), and `legal.legaltags` that exist in the Legal
  service. A record that fails validation returns 400 with the reason.
- **Storage and Search are asynchronous.** A record is stored immediately but
  only appears in Search after the indexer delay (0.7 s default). Search returns
  zero for up to a second after ingest.

Retrieval instructions carry `size` and `sha256` for every known file, and the
reference client aborts if either mismatches the downloaded LAS. Upload tokens
and search cursors expire and are cleaned up lazily.

## Reference example: Well 360

Walks Well -> Wellbore -> WellLog -> Dataset -> LAS through three scenarios.

```bash
./examples/well360/demo/run-tutorial.sh      # record-by-record ingest + LAS upload + Java client
./examples/well360/demo/run-full-sample.sh   # load all 3 Wells + 4 Wellbores + 4 WellLogs + 4 Datasets
./examples/well360/demo/run-manifest.sh      # register Dataset, then ingest a Manifest via Osdu_ingest
```

The Java client (`examples/well360/client/src/main/java/com/beyond/osdu/Well360App.java`)
searches EAGLE-01, reads `W-1001`, resolves Wellbores and WellLogs, fetches the
Dataset retrieval instructions, downloads and checksums the LAS, then runs a
cursor search. It never modifies state.

Record IDs are deterministic (`company-prod:master-data--Well:W-1001`).
`examples/well360/samples/` and the demos use the demo LegalTag
`company-prod-demo-legaltag`; the untouched sources in `reference/` keep the
`<approved-legal-tag-name>` placeholder.

## Repository layout

```text
server/          the emulator (single-file server.py + Docker image)
  composition    compose.yaml (build) and compose.pull.yaml (image)
examples/well360/
  client/        os-core-common Java consumer
  samples/       record JSONs (Well, Wellbore, WellLog, Dataset, Manifest)
  demo/          run-tutorial.sh, run-full-sample.sh, run-manifest.sh
  reference/     the unmodified OSDU tutorial pack the example is based on
```

## Endpoint reference

An OSDU client needs nothing beyond these routes. Full request/response shapes
follow the OSDU OpenAPI specs.

### Health

| Method | Path      | Notes                          |
| ------ | --------- | ------------------------------ |
| GET    | `/health` | status, record counts, profile |

### Entitlements

| Method | Path                                             | Notes                  |
| ------ | ------------------------------------------------ | ---------------------- |
| GET    | `/api/entitlements/v1/groups`                    | `?roleRequired=true`   |
| GET    | `/api/entitlements/v2/groups`                    |                        |
| POST   | `/api/entitlements/v2/groups`                    |                        |
| GET    | `/api/entitlements/v2/groups/{email}/members`    | `?includeType&role=`   |
| GET    | `/api/entitlements/v2/groups/{email}/membersCount` | `?role=`             |
| POST   | `/api/entitlements/v2/groups/{email}/members`    |                        |
| PATCH  | `/api/entitlements/v2/groups/{email}`            | JSON Patch, incl `/appIds` |
| DELETE | `/api/entitlements/v2/groups/{email}`            |                        |
| DELETE | `/api/entitlements/v2/groups/{email}/members/{member}` |                 |
| GET    | `/api/entitlements/v2/members/{email}/groups`    |                        |

### Legal

| Method | Path                                    | Notes                          |
| ------ | --------------------------------------- | ------------------------------ |
| GET    | `/api/legal/v1/legaltags`               |                                |
| POST   | `/api/legal/v1/legaltags`               |                                |
| GET    | `/api/legal/v1/legaltags/{name}`        |                                |
| PUT    | `/api/legal/v1/legaltags`               |                                |
| DELETE | `/api/legal/v1/legaltags/{name}`        |                                |
| GET    | `/api/legal/v1/legaltags:properties`    |                                |
| POST   | `/api/legal/v1/legaltags:validate`      |                                |
| POST   | `/api/legal/v1/legaltags:batchRetrieve` |                                |
| POST   | `/api/legal/v1/legaltags:query`         | `queryList`/`operatorList` (union/intersection/add), `valid`, `between`, `attr=value` |

### Schema service

| Method | Path                                 | Notes          |
| ------ | ------------------------------------ | -------------- |
| GET    | `/api/schema-service/v1/schema`      |                |
| GET    | `/api/schema-service/v1/schema/{kind}` |              |
| POST   | `/api/schema-service/v1/schema`      |                |
| PUT    | `/api/schema-service/v1/schema`      |                |
| PUT    | `/api/schema-service/v1/schemas/system` |             |

### Storage

| Method | Path                                     | Notes                                   |
| ------ | ---------------------------------------- | --------------------------------------- |
| GET    | `/api/storage/v2/query/kinds`            |                                         |
| GET    | `/api/storage/v2/query/records`          | `?kind&limit` -> `{records:[ids], cursor}` |
| GET    | `/api/storage/v2/records`                | `?kind&deleted&sortOrder&modifyAfterDate&cursor&limit` |
| GET    | `/api/storage/v2/records/{id}`           | `?version=` / `?attribute=`             |
| GET    | `/api/storage/v2/records/{id}/{version}` | fixed version read                      |
| GET    | `/api/storage/v2/records/versions/{id}`  | -> `{recordId, versions:[ints]}`        |
| PUT    | `/api/storage/v2/records`                | upsert; returns `{recordCount, recordIds}` |
| PATCH  | `/api/storage/v2/records/{id}`           | in-place merge, version increment        |
| PATCH  | `/api/storage/v2/records`                | collection merge                        |
| POST   | `/api/records/{id}:copy`                 | -> `{recordCount, recordIds, newRecordId}` |
| POST   | `/api/records/{id}:delete`               | single logical delete (204)             |
| POST   | `/api/storage/v2/records/delete`         | batch soft delete (204), non-empty list |
| DELETE | `/api/storage/v2/records`                |                                         |
| DELETE | `/api/storage/v2/records/{id}`           | `?hardDelete=true` deletes permanently   |
| DELETE | `/api/storage/v2/records/versions/{id}`  | purge every version                     |
| POST   | `/api/storage/v2/query/records:batch`    |                                         |

### Search

| Method | Path                                   | Notes             |
| ------ | -------------------------------------- | ----------------- |
| POST   | `/api/search/v2/query`                 | see below         |
| POST   | `/api/search/v2/query_with_cursor`     |                   |
| DELETE | `/api/search/v2/query_with_cursor/{cursor}` |               |
| GET    | `/api/search/v2/liveness_check`        |                   |
| GET    | `/api/search/v2/readiness_check`       |                   |

`POST /api/search/v2/query` supports `offset`, `limit` (max 1000),
`returnedFields`, `kind` (fnmatch patterns), `query` (Lucene-style `AND`/`OR`/
`NOT`, quoted fields, `[a TO b]` ranges, parentheses), `trackTotalCount` (false
omits `totalCount`), `query_as_owner` (only records the caller owns), and
`aggregateBy` with `count`, `filteredTerms`, and `histogram` aggregations.

### Dataset / File

| Method | Path                                           | Notes                                   |
| ------ | ---------------------------------------------- | --------------------------------------- |
| POST   | `/api/dataset/v1/storageInstructions`          | -> `{storageLocation: {signedUrl, token}}` |
| POST   | `/api/dataset/v1/getDatasetRegistry`           | batch by `datasetRegistryIds`           |
| GET    | `/api/dataset/v1/getDatasetRegistry/?id=`      | single                                  |
| POST   | `/api/dataset/v1/retrievalInstructions`        | by `datasetRegistryIds`                 |
| GET    | `/api/dataset/v1/retrievalInstructions`        | `?id=` single; skips revoked URLs       |
| POST   | `/api/dataset/v1/revokeURL`                    | marks storage URLs revoked              |
| POST   | `/api/dataset/v1/metadataRecord/{id}/softDelete` | 204                                   |
| PUT    | `/api/dataset/v1/registerDataset`              | single record or `{datasetRegistries:[...]}` |
| POST   | `/api/file/v2/files`                           | upload a file blob                      |
| GET    | `/api/file/v2/files/{id}`                      | metadata + `fileSourceDatasWithLocation` |
| GET    | `/api/file/v2/files/{id}/downloadURL`          | lower-case download                     |
| GET    | `/api/file/v2/files/{id}/DownloadURL`          | OSDU-cased response, `{signedURL}`      |
| GET    | `/api/file/v2/files/{id}/metadata`             |                                        |
| DELETE | `/api/file/v2/files/{id}/metadata`             |                                        |
| GET    | `/api/file/v2/files/uploadURL`                 |                                        |
| POST   | `/api/file/v2/files/metadata`                  | 201 `{id}`                              |
| POST   | `/api/file/v2/getLocation`                     |                                        |
| POST   | `/api/file/v2/delivery/getFileSignedUrl`       | `{processed, unprocessed}`              |

### Workflow

| Method | Path                                              | Notes             |
| ------ | ------------------------------------------------- | ----------------- |
| GET    | `/api/workflow/v1/workflow`                       | list workflows    |
| POST   | `/api/workflow/v1/workflow`                       | deploy            |
| GET    | `/api/workflow/v1/workflow/{name}`                |                   |
| DELETE | `/api/workflow/v1/workflow/{name}`                |                   |
| GET    | `/api/workflow/v1/workflow/{name}/workflowRun`    | run history       |
| POST   | `/api/workflow/v1/workflow/{name}/workflowRun`    | trigger           |
| GET    | `/api/workflow/v1/workflow/{name}/workflowRun/{runId}` | status + results |
| PUT    | `/api/workflow/v1/workflow/{name}/workflowRun/{runId}` | update         |
| DELETE | `/api/workflow/v1/workflow/{name}/workflowRun/{runId}` |                 |
| POST   | `/api/workflow/v1/workflow/Osdu_ingest/workflowRun` | manifest ingest  |

### POC helpers (not OSDU)

| Method | Path                        | Notes                          |
| ------ | --------------------------- | ------------------------------ |
| POST   | `/poc/reset`                | clear all state                |
| POST   | `/poc/load-full-sample`     | reseed the 15-record sample    |

## Manual ingestion (production style, from zero)

Start empty, then ingest one of each record type with plain `curl`, no POC
helpers. This mirrors what a real client does against a deployment.

```bash
# 1. start empty
sed -i 's/PRELOAD_FULL_SAMPLE: "true"/PRELOAD_FULL_SAMPLE: "false"/' server/compose.yaml
cd server && docker compose down && docker compose up -d --build && cd ..

export OSDU_BASE_URL=http://localhost:8089 OSDU_PARTITION=company-prod OSDU_TOKEN=demo-token
export AUTH="Authorization: Bearer $OSDU_TOKEN"
export PARTITION_HEADER="data-partition-id: $OSDU_PARTITION"

# 2. checks
curl -s -H "$AUTH" -H "$PARTITION_HEADER" "$OSDU_BASE_URL/api/entitlements/v1/groups?roleRequired=true" | jq
curl -s -H "$AUTH" -H "$PARTITION_HEADER" "$OSDU_BASE_URL/api/legal/v1/legaltags" | jq
curl -s -H "$AUTH" -H "$PARTITION_HEADER" "$OSDU_BASE_URL/api/storage/v2/query/kinds?limit=100" | jq

# 3. Dataset storage instructions + LAS upload
export UPLOAD_URL=$(curl -s -X POST "$OSDU_BASE_URL/api/dataset/v1/storageInstructions?kindSubType=dataset--File.Generic" \
  -H "$AUTH" -H "$PARTITION_HEADER" -H "Content-Type: application/json" -d '{}' | jq -r '.storageLocation.signedUrl')
curl -s -X PUT "$UPLOAD_URL" -H "x-file-name: eagle01_gr_cali.las" \
  --data-binary @examples/well360/demo/eagle01_gr_cali.las | jq

# 4. register Dataset + ingest business records via a Manifest
curl -s -X PUT "$OSDU_BASE_URL/api/dataset/v1/registerDataset" \
  -H "$AUTH" -H "$PARTITION_HEADER" -H "Content-Type: application/json" \
  --data-binary @examples/well360/samples/sample_dataset_record.json | jq
curl -s -X POST "$OSDU_BASE_URL/api/workflow/v1/workflow/Osdu_ingest/workflowRun" \
  -H "$AUTH" -H "$PARTITION_HEADER" -H "Content-Type: application/json" \
  --data-binary @examples/well360/samples/manifest_example.json | jq

# 5. read + search
curl -s -H "$AUTH" -H "$PARTITION_HEADER" \
  "$OSDU_BASE_URL/api/storage/v2/records/company-prod:master-data--Well:W-1001" | jq
curl -s -X POST "$OSDU_BASE_URL/api/search/v2/query" \
  -H "$AUTH" -H "$PARTITION_HEADER" -H "Content-Type: application/json" \
  -d '{"kind":"osdu:wks:master-data--Well:*","query":"data.FacilityName:\"EAGLE-01\"","limit":20}' | jq

# 6. retrieval instructions + download + verify the LAS
export DOWNLOAD_URL=$(curl -s -X POST "$OSDU_BASE_URL/api/dataset/v1/retrievalInstructions" \
  -H "$AUTH" -H "$PARTITION_HEADER" -H "Content-Type: application/json" \
  -d '{"datasetRegistryIds":["company-prod:dataset--File.Generic:LOG-1001-GR-LAS"]}' | jq -r '.datasets[0].retrievalProperties.signedUrl')
curl -s "$DOWNLOAD_URL" -o /tmp/eagle01_gr_cali.las && shasum -a 256 /tmp/eagle01_gr_cali.las
```

Step 5 demonstrates the storage/search split: an ingested record is stored at
once but only shows up in Search after the indexer delay (0.7 s). Retry a search
that returns zero after about a second.

## Tests

Zero-dependency unit suite, stdlib `unittest`, no Docker or live server:

```bash
make test
```

73 tests cover: record validation and version history; PATCH, `:copy`, batch and
single delete, hard purge; the search query language and cursor lifecycle; the
indexer delay; Dataset upload -> register -> retrieval with size + SHA-256;
batch register, revokeURL, soft delete; File v2 metadata/uploadURL/delivery;
Workflow deploy/list/run and manifest ingest; LegalTag CRUD, validate, and
`:query` operators; Schema list/create/system; entitlements membersCount/appIds;
RBAC 403s; token and cursor TTL cleanup.

CI (`.github/workflows/ci.yml`) runs the unit suite, starts the emulator for
`smoke-test.sh`, and runs the client contract check (pinned os-core-common
3.1.0 API surface, fully offline - the OSDU community Maven registry has been
flaky since the OSDU->OpenDES hosting migration). A second workflow builds and
publishes the multi-arch container image to GHCR.

## Troubleshooting

| Symptom                              | Cause and fix                                                          |
| ------------------------------------ | --------------------------------------------------------------------- |
| 401                                  | use `Authorization: Bearer demo-token`                                 |
| 400                                  | use `data-partition-id: company-prod`                                  |
| 403                                  | token is not in the required entitlement; the demo token has all groups |
| 400 on ingest                        | record failed validation - missing `acl`/`legal`, unknown kind or LegalTag |
| EAGLE-01 not found                  | data not loaded; check `/health`                                       |
| in Storage, missing from Search      | indexer delay, wait ~1 s and retry                                     |
| no LAS downloadable                  | Dataset missing: `GET /api/dataset/v1/getDatasetRegistry/?id=company-prod:dataset--File.Generic:LOG-1001-GR-LAS` |
| `mvn` cannot resolve `os-core-common` | the artifact is only on the OSDU community registry (`https://community.opengroup.org/api/v4/projects/67/packages/maven`), which has been flaky during the OSDU->OpenDES hosting migration; retry `mvn -U clean compile` or wait for the registry. CI does not depend on it (`client/check-client.sh`). |

## Moving to a real OSDU

Swap the defaults for client-provided values: base URL, partition, bearer token,
ACL groups, approved LegalTags. The Java Search/Storage code stays as is; confirm
the real deployment's routes, schema versions, and OpenAPI before ingesting.
Production hardening (OIDC, TLS, retries, metrics) is intentionally out of scope.

## License

Apache License 2.0 - see [LICENSE](LICENSE). Copyright 2026 Sohaib Qasem.