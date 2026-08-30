# Changelog

All notable changes to OSDU-Lite are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.7.0-beta.1] - 2026-08-29

First beta release. Closes the remaining official-API surface gaps of the
in-memory emulator; all endpoints match the real OSDU request/response shapes.

### Added
- **Storage v2**
  - Version history: `GET /api/storage/v2/records/versions/{id}` returns
    `{recordId, versions: [ints]}`, `GET {id}/{version}` returns a specific
    version, `?version=` on the single-record read.
  - `PATCH /api/storage/v2/records/{id}` (single) and `PATCH /api/storage/v2/records`
    (collection) with in-place data/ACL/legal merge and version increments.
  - `POST /api/records/{id}:copy` returns `{recordCount, recordIds, newRecordId}`.
  - `POST /api/records/{id}:delete` (single logical delete, 204) and
    `POST /api/storage/v2/records/delete` (batch soft delete, 204).
  - `DELETE /api/storage/v2/records/versions/{id}` hard-purges every version.
  - `GET /api/storage/v2/query/records` returns `{records: [ids], cursor}`.
  - `?attribute=` projection on single-record reads.
- **Legal v1**
  - `POST /api/legal/v1/legaltags:query` supporting `queryList`/`operatorList`
    (`union`, `intersection`, `add`), the `valid` filter, exclusive `between`
    date ranges, `attr=value` and free-text search over fields plus nested
    `extensionProperties`.
- **File v2**
  - Metadata: `POST /api/file/v2/files/metadata` (201 `{id}`),
    `GET / DELETE /api/file/v2/files/{id}/metadata`.
  - `GET /api/file/v2/files/uploadURL`, `POST /api/file/v2/getLocation` (returns
    a signed upload URL), `POST /api/file/v2/delivery/getFileSignedUrl`
    (`{processed, unprocessed}`), and the capital-D `DownloadURL` response
    (`{signedURL}`).
- **Dataset v1**
  - Batch `POST /api/dataset/v1/getDatasetRegistry` (`datasetRegistryIds`).
  - `POST /api/dataset/v1/revokeURL` (revoked URLs are excluded from later
    retrieval instructions).
  - `POST /api/dataset/v1/metadataRecord/{id}/softDelete` (204).
  - `GET /api/dataset/v1/retrievalInstructions?id=` (single) and batch
    `PUT /api/dataset/v1/registerDataset` with `{"datasetRegistries": [...]}`.
- **Workflow v1**
  - `GET /api/workflow/v1/workflow` (list) and `POST /api/workflow/v1/workflow`
    (deploy), `GET / DELETE /api/workflow/v1/workflow/{name}`.
  - Run history `GET .../workflowRun`, `PUT .../workflowRun/{runId}` updates,
    `DELETE .../workflowRun/{runId}`, and generic workflow triggers. The
    Osdu_ingest manifest-ingest flow is preserved.
- **Schema-service v1**
  - `GET / POST /api/schema-service/v1/schema` (list/create),
    `PUT /api/schema-service/v1/schema`, `PUT /api/schema-service/v1/schemas/system`,
    and single `GET /api/schema-service/v1/schema/{kind}` now also serves
    registered community schemas.
- **Entitlements v1/v2**
  - `GET /api/entitlements/v2/groups/{email}/membersCount` with optional `?role=`.
  - v1 `GET /groups?roleRequired=true` returns group roles.
  - v2 `GET /groups` responses include `desId`/`memberEmail`; group `PATCH`
    supports JSON Patch including `/appIds`.
- **Search v2**
  - `DELETE /api/search/v2/query_with_cursor/{cursor}` (204) and
    `GET /api/search/v2/liveness_check` / `readiness_check` (`{"status": "UP"}`).

### Fixed
- Entitlements prefix matching now guards on `path.startswith(...)`, so unrelated
  DELETE requests (e.g. workflow, file) are no longer swallowed by the group/member
  handlers.
- Storage hard delete and purge now also clear the record version history.

### Changed
- Server version bumped `0.6` -> `0.7`; endpoint map in `README.md` updated.

### Tests
- 73 unit tests passing (16 new), covering storage versioning/patch/copy/purge,
  legal query operators, file v2 metadata/delivery, dataset batch register/revoke/
  softDelete, workflow deploy/run lifecycle, schema list/create/system, and
  entitlements membersCount/appIds.

## [0.6] - 2026-08-29

Extended the emulator API surface to match the real OSDU services exercised by
the reference example and the official client path coverage verdict.

### Added
- Storage: batch soft delete (`DELETE /records`), single hard delete
  (`DELETE /records/{id}?hardDelete=true`), `modifyAfterDate` filtering, cursor
  paging on the records list, and `getKinds`.
- Search: `trackTotalCount`, `query_as_owner`, and `aggregateBy` with `count`,
  `filteredTerms`, and `histogram` aggregations.
- Legal: `legaltags:validate`, `legaltags:batchRetrieve`,
  `legaltags:properties`, per-tag GET/DELETE, and validity from `expirationDate`.
- File v2: `POST /files`, `GET /files/{id}` with `fileSourceDatasWithLocation`
  and SHA-256/size, and `GET /files/{id}/downloadURL`.
- Workflow: Osdu_ingest manifest-based ingestion trigger with `recordCount` /
  `recordIds` and pollable run status.
- Entitlements v2: group/member create, list, members, delete.
- `do_DELETE` and `do_PATCH` HTTP methods, plus Docker compose and smoke tests.

## [0.5] - 2026-08-29

Initial OSDU-Lite emulator: Storage PUT/GET/search, legal tags, entitlements
read, Dataset storage/retrieval instructions and LAS upload, the Osdu_ingest
workflow, and the Well 360 reference example with the `os-core-common` Java
client.

- Versioned record upserts and simulated indexer delay.
- Lucene-style search queries with cursor paging.
- Deterministic record IDs and a preloaded full sample.

[v0.7.0-beta.1]: https://github.com/sohaibqasem/osdu-lite/releases/tag/v0.7.0-beta.1