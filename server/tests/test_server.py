import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PRELOAD_FULL_SAMPLE", "false")
os.environ.setdefault("REQUIRE_AUTH", "true")
os.environ.setdefault("OSDU_TOKEN", "demo-token")
os.environ.setdefault("OSDU_PARTITION", "company-prod")
os.environ.setdefault("INDEX_DELAY_SECONDS", "0")

import server  # noqa: E402

METHODS = {"get": "do_GET", "post": "do_POST", "put": "do_PUT", "delete": "do_DELETE",
           "patch": "do_PATCH", "options": "do_OPTIONS"}


class _Headers:
    def __init__(self, values):
        self._d = {str(k).lower(): v for k, v in values.items()}

    def get(self, key, default=None):
        return self._d.get(str(key).lower(), default)


class _FakeWriter:
    def __init__(self):
        self.buf = io.BytesIO()

    def write(self, data):
        self.buf.write(data)


class _FakeHandler:
    def __init__(self, path, headers, body):
        self.path = path
        self.headers = headers
        self.rfile = io.BytesIO((body if isinstance(body, bytes) else body.encode("utf-8")))
        self.wfile = _FakeWriter()
        self.client_address = ("127.0.0.1", 0)
        self._status = None
        self._response_headers = []

    def send_response(self, code):
        self._status = code

    def send_header(self, key, value):
        self._response_headers.append((key, value))

    def end_headers(self):
        pass


def make_handler(path, headers, body):
    handler = object.__new__(server.Handler)
    handler.path = path
    handler.headers = headers
    handler.rfile = io.BytesIO((body if isinstance(body, bytes) else body.encode("utf-8")))
    handler.wfile = _FakeWriter()
    handler.client_address = ("127.0.0.1", 0)
    handler.requestline = "HTTP/1.1 200 OK"
    handler.request_version = "HTTP/1.1"
    handler._status = None
    handler._response_headers = []
    handler.send_response = lambda code: setattr(handler, "_status", code)
    handler.send_response_only = lambda *a, **k: None
    handler.send_header = lambda k, v: handler._response_headers.append((k, v))
    handler.end_headers = lambda: None
    handler.log_request = lambda *a, **k: None
    return handler


def call(method, path, body=None, token="demo-token", partition="company-prod", raw=False, headers=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "data-partition-id": partition,
        "Host": "localhost:TEST",
        **(headers or {}),
    }
    payload = None
    body_text = ""
    if body is not None:
        if raw:
            payload = body if isinstance(body, bytes) else body.encode("utf-8")
        else:
            payload = body if isinstance(body, str) else json.dumps(body)
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload))
        body_text = payload
    fake = make_handler(path, _Headers(headers), body_text)
    getattr(server.Handler, METHODS[method])(fake)
    data = fake.wfile.buf.getvalue()
    try:
        parsed = json.loads(data.decode("utf-8")) if data else None
    except Exception:
        parsed = None
    return fake._status, parsed


def wait_until(fn, timeout=3.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval)
    return bool(fn())


def valid_well(rid="company-prod:master-data--Well:WT-1", name="EAGLE-01"):
    return {
        "id": rid,
        "kind": server.WELL_KIND,
        "acl": {"viewers": [server.ACL_VIEWERS], "owners": [server.ACL_OWNERS]},
        "legal": {"legaltags": [server.LEGAL_TAG], "otherRelevantDataCountries": ["SA"]},
        "data": {"FacilityName": name, "FieldName": "EAGLE-A", "Status": "DW"},
    }


def valid_log(rid="company-prod:work-product-component--WellLog:WL-1",
              name="EAGLE-01", start_m=1500.0):
    return {
        "id": rid,
        "kind": server.WELLLOG_KIND,
        "acl": {"viewers": [server.ACL_VIEWERS], "owners": [server.ACL_OWNERS]},
        "legal": {"legaltags": [server.LEGAL_TAG], "otherRelevantDataCountries": ["SA"]},
        "data": {"FacilityName": name, "StartM": start_m, "StopM": start_m + 500},
    }


class ServerTestCase(unittest.TestCase):

    def setUp(self):
        server.clear_state()
        server.INDEX_DELAY_SECONDS = 0.0
        self._tmp_files = tempfile.TemporaryDirectory()
        server.FILES_DIR = Path(self._tmp_files.name)

    def tearDown(self):
        server.clear_state()
        server.INDEX_DELAY_SECONDS = 0.0
        server.INDEX_DELAY_SECONDS = float(os.environ.get("INDEX_DELAY_SECONDS", "0"))
        self._tmp_files.cleanup()

    def put_records(self, records, **kw):
        return call("put", "/api/storage/v2/records", records, **kw)


class TestHealth(ServerTestCase):

    def test_health_empty(self):
        status, payload = call("get", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "UP")
        self.assertEqual(payload["storedRecords"], 0)
        self.assertEqual(payload["currentDataProfile"], "empty")

    def test_health_public_no_auth(self):
        status, _ = call("get", "/health", token="")
        self.assertEqual(status, 200)


class TestStorageValidation(ServerTestCase):

    def test_put_get_roundtrip(self):
        status, payload = self.put_records([valid_well()])
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordCount"], 1)
        status, rec = call("get", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 200)
        self.assertEqual(rec["version"], 1)
        self.assertEqual(rec["data"]["FacilityName"], "EAGLE-01")

    def test_put_rejects_without_acl(self):
        rec = valid_well()
        del rec["acl"]
        status, payload = self.put_records([rec])
        self.assertEqual(status, 400)
        self.assertIn("acl", payload["message"])

    def test_put_rejects_without_legal(self):
        rec = valid_well()
        del rec["legal"]
        status, payload = self.put_records([rec])
        self.assertEqual(status, 400)
        self.assertIn("legal.legaltags", payload["message"])

    def test_put_rejects_unknown_kind(self):
        rec = valid_well()
        rec["kind"] = "osdu:wks:master-data--Foo:1.0.0"
        status, payload = self.put_records([rec])
        self.assertEqual(status, 400)
        self.assertIn("Unsupported kind", payload["message"])

    def test_put_rejects_unknown_legaltag(self):
        rec = valid_well()
        rec["legal"]["legaltags"] = ["company-prod-nope"]
        status, payload = self.put_records([rec])
        self.assertEqual(status, 400)
        self.assertIn("unknown LegalTag", payload["message"])

    def test_put_version_increments(self):
        self.assertEqual(self.put_records([valid_well()])[0], 200)
        status, payload = self.put_records([valid_well()])
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordIds"][0]["version"], 2)

    def test_invalid_json_body(self):
        status, payload = call("post", "/api/search/v2/query", body="{ not json", raw=True)
        self.assertEqual(status, 400)
        self.assertIn("Invalid JSON body", payload["message"])


class TestSearch(ServerTestCase):

    def search(self, payload, **kw):
        return call("post", "/api/search/v2/query", payload, **kw)

    def test_search_requires_kind(self):
        self.put_records([valid_well()])
        status, payload = self.search({})
        self.assertEqual(status, 200)
        self.assertEqual(payload["totalCount"], 0)

    def test_search_with_index_delay(self):
        server.INDEX_DELAY_SECONDS = 2.0
        self.put_records([valid_well()])
        _, payload = self.search({"kind": server.WELL_KIND})
        self.assertEqual(payload["totalCount"], 0)
        self.assertTrue(wait_until(
            lambda: self.search({"kind": server.WELL_KIND})[1]["totalCount"] == 1,
            timeout=10.0))

    def test_search_field_query(self):
        self.put_records([valid_well(name="EAGLE-01"), valid_well("company-prod:master-data--Well:WT-2", "OSPREY-02")])
        wait_until(lambda: self.search({"kind": server.WELL_KIND})[1]["totalCount"] == 2)
        _, payload = self.search({"kind": server.WELL_KIND, "query": 'data.FacilityName:"EAGLE-01"'})
        self.assertEqual(payload["totalCount"], 1)
        self.assertEqual(payload["results"][0]["data"]["FacilityName"], "EAGLE-01")
        _, payload = self.search({"kind": server.WELL_KIND, "query": 'data.FacilityName:"NO-SUCH"'})
        self.assertEqual(payload["totalCount"], 0)

    def test_search_and_or_range(self):
        self.put_records([
            valid_log("company-prod:work-product-component--WellLog:L1", "EAGLE-01", 1000.0),
            valid_log("company-prod:work-product-component--WellLog:L2", "OSPREY-02", 3000.0),
        ])
        wait_until(lambda: self.search({"kind": server.WELLLOG_KIND})[1]["totalCount"] == 2)
        _, payload = self.search({
            "kind": server.WELLLOG_KIND,
            "query": 'data.FacilityName:"EAGLE-01" AND data.StartM:[500 TO 2000]',
        })
        self.assertEqual(payload["totalCount"], 1)
        _, payload = self.search({
            "kind": server.WELLLOG_KIND,
            "query": 'data.FacilityName:"EAGLE-01" OR data.FacilityName:"OSPREY-02"',
        })
        self.assertEqual(payload["totalCount"], 2)
        _, payload = self.search({
            "kind": server.WELLLOG_KIND,
            "query": 'data.StartM:[2500 TO *]',
        })
        self.assertEqual(payload["totalCount"], 1)
        _, payload = self.search({
            "kind": server.WELLLOG_KIND,
            "query": 'NOT data.FacilityName:"OSPREY-02"',
        })
        self.assertEqual(payload["totalCount"], 1)

    def test_cursor_paging(self):
        for i in range(3):
            self.put_records([valid_well(f"company-prod:master-data--Well:WT-{i}")])
        wait_until(lambda: self.search({"kind": server.WELL_KIND})[1]["totalCount"] == 3)
        _, first = call("post", "/api/search/v2/query_with_cursor",
                        {"kind": server.WELL_KIND, "limit": 2})
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(first["totalCount"], 3)
        cursor = first["cursor"]
        _, second = call("post", "/api/search/v2/query_with_cursor", {"cursor": cursor})
        self.assertEqual(len(second["results"]), 1)
        self.assertEqual(second["totalCount"], 3)
        cursor = second["cursor"]
        _, third = call("post", "/api/search/v2/query_with_cursor", {"cursor": cursor})
        self.assertEqual(len(third["results"]), 0)

    def test_bad_cursor(self):
        status, payload = call("post", "/api/search/v2/query_with_cursor", {"cursor": "stale"})
        self.assertEqual(status, 400)
        self.assertIn("cursor", payload["message"].lower())


class TestDataset(ServerTestCase):

    def test_dataset_lifecycle_with_checksums(self):
        status, payload = call("post", "/api/dataset/v1/storageInstructions", {})
        self.assertEqual(status, 200)
        url = payload["storageLocation"]["signedUrl"]
        token = url.rsplit("/", 1)[1]
        body = b"LAS-001 sample curve data"
        status, upload = call("put", f"/upload/{token}", body=body, raw=True,
                              token="", headers={"x-file-name": "up-1.las"})
        self.assertEqual(status, 200)
        self.assertEqual(upload["size"], len(body))
        self.assertEqual(upload["sha256"], hashlib.sha256(body).hexdigest())

        did = "company-prod:dataset--File.Generic:UP-1-LAS"
        ds = valid_well(did)
        ds["kind"] = server.DATASET_KIND
        ds["data"] = {"Name": "up-1.las", "FileName": "up-1.las", "Format": "LAS"}
        self.assertEqual(self.put_records([ds])[0], 200)

        status, payload = call("post", "/api/dataset/v1/retrievalInstructions",
                               {"datasetRegistryIds": [did]})
        self.assertEqual(status, 200)
        props = payload["datasets"][0]["retrievalProperties"]
        self.assertEqual(props["size"], len(body))
        self.assertEqual(props["sha256"], hashlib.sha256(body).hexdigest())

    def test_upload_requires_valid_token(self):
        status, _ = call("put", "/upload/bad-token", body=b"data", raw=True, token="")
        self.assertEqual(status, 404)

    def test_storage_instructions_expiry_reported(self):
        server.UPLOAD_TOKEN_TTL = 120
        _, payload = call("post", "/api/dataset/v1/storageInstructions", {})
        self.assertEqual(payload["storageLocation"]["expiresInSeconds"], 120)
        server.UPLOAD_TOKEN_TTL = 3600

    def test_upload_token_ttl_sweep(self):
        server.UPLOAD_TOKEN_TTL = 10
        with server.STORE_LOCK:
            server.UPLOAD_TOKENS["stale-token"] = {"created": time.time() - 1000, "kindSubType": "x"}
        _, payload = call("post", "/api/dataset/v1/storageInstructions", {})
        with server.STORE_LOCK:
            self.assertNotIn("stale-token", server.UPLOAD_TOKENS)
            self.assertIn(payload["storageLocation"]["signedUrl"].rsplit("/", 1)[1], server.UPLOAD_TOKENS)
        server.UPLOAD_TOKEN_TTL = 3600

    def test_full_sample_retrieval_has_checksums(self):
        call("post", "/poc/load-full-sample")
        with server.STORE_LOCK:
            did = next(rid for rid, r in server.RECORD_BY_ID.items() if r["kind"] == server.DATASET_KIND)
        _, payload = call("post", "/api/dataset/v1/retrievalInstructions", {"datasetRegistryIds": [did]})
        props = payload["datasets"][0]["retrievalProperties"]
        self.assertIn("size", props)
        self.assertIn("sha256", props)
        self.assertEqual(len(props["sha256"]), 64)


class TestWorkflow(ServerTestCase):

    def manifest(self, items, acl=None, legal=None):
        return {
            "executionContext": {
                "acl": acl or {"viewers": [server.ACL_VIEWERS], "owners": [server.ACL_OWNERS]},
                "legal": legal or {"legaltags": [server.LEGAL_TAG], "otherRelevantDataCountries": ["SA"]},
                "manifest": {"MasterData": items, "Data": {}},
            }
        }

    def test_submit_and_poll_success(self):
        server.INDEX_DELAY_SECONDS = 0.3
        status, payload = call("post", "/api/workflow/v1/workflow/Osdu_ingest/workflowRun",
                               self.manifest([valid_well()]))
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "submitted")
        run_id = payload["workflowRunId"]
        status, run = call("get", f"/api/workflow/v1/workflow/{run_id}")
        self.assertEqual(status, 200)
        self.assertIn(run["status"], {"running", "succeeded"})
        status, _ = call("get", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 200)
        time.sleep(0.6)
        _, run = call("get", f"/api/workflow/v1/workflow/{run_id}")
        self.assertEqual(run["status"], "succeeded")

    def test_submit_rejects_invalid_manifest(self):
        rec = valid_well()
        del rec["acl"]
        body = {"executionContext": {"manifest": {"MasterData": [rec], "Data": {}}}}
        status, payload = call("post", "/api/workflow/v1/workflow/Osdu_ingest/workflowRun", body)
        self.assertEqual(status, 400)
        self.assertIn("acl", payload["message"])

    def test_unknown_run(self):
        status, _ = call("get", "/api/workflow/v1/workflow/wf-nope")
        self.assertEqual(status, 404)


class TestLegal(ServerTestCase):

    def test_create_and_use_tag(self):
        status, payload = call("post", "/api/legal/v1/legaltags", {"name": "company-prod-extra-tag"})
        self.assertEqual(status, 201)
        self.assertEqual(payload["name"], "company-prod-extra-tag")
        self.assertTrue(payload["isValid"])
        status, _ = call("get", "/api/legal/v1/legaltags/company-prod-extra-tag")
        self.assertEqual(status, 200)
        rec = valid_well()
        rec["legal"]["legaltags"] = ["company-prod-extra-tag"]
        self.assertEqual(self.put_records([rec])[0], 200)
        _, payload = call("get", "/api/legal/v1/legaltags")
        names = [t["name"] for t in payload["legalTags"]]
        self.assertIn("company-prod-extra-tag", names)
        self.assertIn(server.LEGAL_TAG, names)

    def test_unknown_tag_404(self):
        status, _ = call("get", "/api/legal/v1/legaltags/company-prod-nope")
        self.assertEqual(status, 404)


class TestRbac(ServerTestCase):

    def test_write_requires_editor_group(self):
        saved = server.GROUPS_BY_TOKEN[server.TOKEN]
        server.GROUPS_BY_TOKEN[server.TOKEN] = {"users.datalake.viewers"}
        try:
            status, payload = self.put_records([valid_well()])
            self.assertEqual(status, 403)
            self.assertIn("Insufficient entitlements", payload["message"])
        finally:
            server.GROUPS_BY_TOKEN[server.TOKEN] = saved

    def test_search_requires_search_user(self):
        saved = server.GROUPS_BY_TOKEN[server.TOKEN]
        server.GROUPS_BY_TOKEN[server.TOKEN] = {"users.datalake.editors"}
        try:
            status, _ = call("post", "/api/search/v2/query", {"kind": server.WELL_KIND})
            self.assertEqual(status, 403)
        finally:
            server.GROUPS_BY_TOKEN[server.TOKEN] = saved

    def test_full_token_passes_all(self):
        self.assertEqual(self.put_records([valid_well()])[0], 200)
        status, _ = call("post", "/api/search/v2/query", {"kind": server.WELL_KIND})
        self.assertEqual(status, 200)


class TestPoc(ServerTestCase):

    def test_load_and_reset(self):
        status, payload = call("post", "/poc/load-full-sample")
        self.assertEqual(status, 200)
        self.assertEqual(payload["storedRecords"], len(server.FULL_SAMPLE))
        self.assertEqual(payload["currentDataProfile"], "full-sample")
        status, payload = call("post", "/poc/reset")
        self.assertEqual(status, 200)
        self.assertEqual(payload["storedRecords"], 0)
        self.assertEqual(payload["currentDataProfile"], "empty")
        self.assertEqual(payload["recordsByKind"]["wells"], 0)


class TestStorageDeleteAndPaging(ServerTestCase):

    def test_batch_soft_delete(self):
        self.put_records([valid_well(), valid_well("company-prod:master-data--Well:WT-2", "OSPREY-02")])
        self.put_records([valid_log("company-prod:work-product-component--WellLog:L1")])
        status, payload = call("delete", "/api/storage/v2/records",
                               {"records": ["company-prod:master-data--Well:WT-1"]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordCount"], 1)
        self.assertEqual(payload["skippedRecordIds"], [])
        status, _ = call("get", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 404)
        status, payload = call("get", "/api/storage/v2/records?kind=" + server.WELL_KIND)
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in payload["records"]], ["company-prod:master-data--Well:WT-2"])
        status, payload = call("get", "/api/storage/v2/records?kind=" + server.WELL_KIND + "&deleted=true")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["records"]), 1)
        self.assertEqual(payload["records"][0]["id"], "company-prod:master-data--Well:WT-1")

    def test_soft_deleted_record_leaves_search(self):
        self.put_records([valid_well()])
        wait_until(lambda: call("post", "/api/search/v2/query",
                                {"kind": server.WELL_KIND})[1]["totalCount"] == 1)
        call("delete", "/api/storage/v2/records", {"records": ["company-prod:master-data--Well:WT-1"]})
        time.sleep(0.1)
        _, payload = call("post", "/api/search/v2/query", {"kind": server.WELL_KIND})
        self.assertEqual(payload["totalCount"], 0)

    def test_reupsert_revives_deleted(self):
        self.put_records([valid_well()])
        call("delete", "/api/storage/v2/records", {"records": ["company-prod:master-data--Well:WT-1"]})
        status, _ = call("get", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 404)
        self.put_records([valid_well()])
        status, rec = call("get", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 200)
        self.assertEqual(rec["version"], 2)

    def test_single_delete_and_hard_delete(self):
        self.put_records([valid_well()])
        status, _ = call("delete", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 204)
        self.put_records([valid_well()])
        status, _ = call("delete", "/api/storage/v2/records/company-prod:master-data--Well:WT-1?hardDelete=true")
        self.assertEqual(status, 204)
        with server.STORE_LOCK:
            self.assertNotIn("company-prod:master-data--Well:WT-1", server.RECORD_BY_ID)
        status, _ = call("get", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 404)
        status, _ = call("delete", "/api/storage/v2/records/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 404)

    def test_list_cursor_paging(self):
        for i in range(3):
            self.put_records([valid_well(f"company-prod:master-data--Well:WT-{i}")])
        _, first = call("get", "/api/storage/v2/records?limit=2")
        self.assertEqual(len(first["records"]), 2)
        self.assertIsNotNone(first["cursor"])
        _, second = call("get",
                         "/api/storage/v2/records?cursor=" + first["cursor"])
        self.assertEqual(len(second["records"]), 1)
        self.assertIsNone(second["cursor"])

    def test_bad_list_cursor(self):
        status, _ = call("get", "/api/storage/v2/records?cursor=stale")
        self.assertEqual(status, 400)

    def test_sort_order_desc(self):
        self.put_records([valid_well("company-prod:master-data--Well:WT-1"),
                          valid_well("company-prod:master-data--Well:WT-5", "Z"),
                          valid_well("company-prod:master-data--Well:WT-3", "M")])
        _, payload = call("get", "/api/storage/v2/records?sortOrder=desc")
        ids = [r["id"] for r in payload["records"]]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_modify_after_date(self):
        self.put_records([valid_well()])
        with server.STORE_LOCK:
            server.RECORD_TS["company-prod:master-data--Well:WT-1"]["modify"] = 1_700_000_000
        _, payload = call("get", "/api/storage/v2/records?modifyAfterDate=2023-11-14T22:13:20Z")
        self.assertEqual(len(payload["records"]), 1)
        _, payload = call("get", "/api/storage/v2/records?modifyAfterDate=2023-11-16T22:13:20Z")
        self.assertEqual(len(payload["records"]), 0)


class TestLegalExtended(ServerTestCase):

    def expired_tag(self):
        return {"name": "company-prod-expired",
                "properties": {"expirationDate": "2000-01-01"}}

    def test_create_expired_is_invalid_and_listable(self):
        status, payload = call("post", "/api/legal/v1/legaltags", self.expired_tag())
        self.assertEqual(status, 201)
        self.assertFalse(payload["isValid"])
        _, payload = call("get", "/api/legal/v1/legaltags")
        names = [t["name"] for t in payload["legalTags"]]
        self.assertNotIn("company-prod-expired", names)
        _, payload = call("get", "/api/legal/v1/legaltags?valid=false")
        names = [t["name"] for t in payload["legalTags"]]
        self.assertIn("company-prod-expired", names)

    def test_put_update_restores_validity(self):
        call("post", "/api/legal/v1/legaltags", self.expired_tag())
        status, payload = call("put", "/api/legal/v1/legaltags",
                               {"name": "company-prod-expired",
                                "properties": {"expirationDate": "2099-01-01"}})
        self.assertEqual(status, 200)
        self.assertTrue(payload["isValid"])
        rec = valid_well()
        rec["legal"]["legaltags"] = ["company-prod-expired"]
        self.assertEqual(self.put_records([rec])[0], 200)

    def test_ingest_rejects_expired_tag(self):
        call("post", "/api/legal/v1/legaltags", self.expired_tag())
        rec = valid_well()
        rec["legal"]["legaltags"] = ["company-prod-expired"]
        status, payload = self.put_records([rec])
        self.assertEqual(status, 400)
        self.assertIn("not valid", payload["message"])

    def test_delete_tag(self):
        call("post", "/api/legal/v1/legaltags", {"name": "company-prod-temp"})
        status, _ = call("delete", "/api/legal/v1/legaltags/company-prod-temp")
        self.assertEqual(status, 204)
        status, _ = call("get", "/api/legal/v1/legaltags/company-prod-temp")
        self.assertEqual(status, 404)

    def test_validate_endpoint(self):
        _, payload = call("post", "/api/legal/v1/legaltags:validate",
                          {"legaltags": [server.LEGAL_TAG, "company-prod-missing"]})
        self.assertEqual(payload["invalidLegalTags"], [{"name": "company-prod-missing",
                                                        "reason": "LegalTag not found"}])

    def test_batch_retrieve(self):
        _, payload = call("post", "/api/legal/v1/legaltags:batchRetrieve",
                          {"name": [server.LEGAL_TAG]})
        self.assertEqual(len(payload["legalTags"]), 1)
        self.assertEqual(payload["legalTags"][0]["name"], server.LEGAL_TAG)

    def test_properties_endpoint(self):
        _, payload = call("get", "/api/legal/v1/legaltags:properties")
        names = {p["propertyName"] for p in payload["legaltagProperties"]}
        self.assertIn("countryOfOrigin", names)
        self.assertIn("expirationDate", names)

    def test_duplicate_create_409(self):
        status, _ = call("post", "/api/legal/v1/legaltags", {"name": server.LEGAL_TAG})
        self.assertEqual(status, 409)


class TestSearchEnhanced(ServerTestCase):

    def test_track_total_count_false_omits(self):
        self.put_records([valid_well()])
        def search_no_total():
            _, p = call("post", "/api/search/v2/query",
                        {"kind": server.WELL_KIND, "trackTotalCount": False})
            return p
        wait_until(lambda: len(search_no_total()["results"]) == 1)
        payload = search_no_total()
        self.assertNotIn("totalCount", payload)
        self.assertEqual(len(payload["results"]), 1)

    def test_query_as_owner_restricts(self):
        rec = valid_well()
        rec["acl"]["owners"] = ["data.other.owners@company-prod.company.com"]
        self.put_records([rec])
        wait_until(lambda: call("post", "/api/search/v2/query",
                                {"kind": server.WELL_KIND})[1]["totalCount"] == 1)
        _, payload = call("post", "/api/search/v2/query",
                          {"kind": server.WELL_KIND, "query_as_owner": True})
        self.assertEqual(payload["totalCount"], 0)

    def test_aggregate_count_and_terms(self):
        for i in range(3):
            self.put_records([valid_well(f"company-prod:master-data--Well:WT-{i}")])
        wait_until(lambda: call("post", "/api/search/v2/query",
                                {"kind": server.WELL_KIND})[1]["totalCount"] == 3)
        _, payload = call("post", "/api/search/v2/query", {
            "kind": server.WELL_KIND,
            "aggregateBy": [
                {"aggregateByType": "count", "field": "kind"},
                {"aggregateByType": "filteredTerms", "field": "kind", "bucketSize": 10},
            ],
        })
        self.assertEqual(payload["aggregates"][0]["Count"]["count"], 3)
        terms = payload["aggregates"][1]["FilteredTerms"]["terms"]
        self.assertEqual(terms[0]["key"], server.WELL_KIND)
        self.assertEqual(terms[0]["count"], 3)

    def test_aggregate_histogram(self):
        self.put_records([valid_log("company-prod:work-product-component--WellLog:L1", "A", 1000.0),
                          valid_log("company-prod:work-product-component--WellLog:L2", "B", 3000.0)])
        wait_until(lambda: call("post", "/api/search/v2/query",
                                {"kind": server.WELLLOG_KIND})[1]["totalCount"] == 2)
        _, payload = call("post", "/api/search/v2/query", {
            "kind": server.WELLLOG_KIND,
            "aggregateBy": [{"aggregateByType": "histogram", "field": "data.StartM", "interval": 1000}],
        })
        buckets = payload["aggregates"][0]["Histogram"]["buckets"]
        self.assertEqual(len(buckets), 2)
        self.assertEqual(buckets[0]["start"], 1000)
        self.assertEqual(buckets[1]["count"], 1)


class TestFileService(ServerTestCase):

    def _register_file(self, body):
        status, payload = call("put", f"/upload/{body[0]}", body=body[1], raw=True,
                               token="", headers={"x-file-name": "f-1.las"})
        self.assertEqual(status, 200)
        return payload

    def test_post_get_download(self):
        status, instructions = call("post", "/api/dataset/v1/storageInstructions", {})
        token = instructions["storageLocation"]["signedUrl"].rsplit("/", 1)[1]
        content = b"f-1 curve data"
        upload = self._register_file((token, content))
        rec = valid_well("company-prod:dataset--File.Generic:F-1-LAS")
        rec["kind"] = server.DATASET_KIND
        rec["data"] = {"Name": "f-1.las", "FileName": "f-1.las", "Format": "LAS"}
        status, payload = call("post", "/api/file/v2/files", rec)
        self.assertEqual(status, 200)
        self.assertEqual(payload["fileID"], "company-prod:dataset--File.Generic:F-1-LAS")
        self.assertEqual(payload["fileSourceDatasWithLocation"][0]["fileSource"], "f-1.las")

        status, payload = call("get", "/api/file/v2/files/company-prod:dataset--File.Generic:F-1-LAS")
        self.assertEqual(status, 200)
        loc = payload["fileSourceDatasWithLocation"][0]["location"]
        self.assertEqual(loc["signedUrl"].endswith("/files/f-1.las"), True)
        self.assertEqual(loc["sha256"], hashlib.sha256(content).hexdigest())

        status, payload = call("get", "/api/file/v2/files/company-prod:dataset--File.Generic:F-1-LAS/downloadURL")
        self.assertEqual(status, 200)
        self.assertEqual(payload["size"], len(content))
        self.assertEqual(payload["sha256"], hashlib.sha256(content).hexdigest())

    def test_get_full_sample_file(self):
        call("post", "/poc/load-full-sample")
        with server.STORE_LOCK:
            did = next(rid for rid, r in server.RECORD_BY_ID.items() if r["kind"] == server.DATASET_KIND)
        status, payload = call("get", f"/api/file/v2/files/{did}/downloadURL")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["sha256"]), 64)

    def test_post_requires_dataset_record(self):
        status, _ = call("post", "/api/file/v2/files", {"name": "not-a-record"})
        self.assertEqual(status, 400)

    def test_get_non_dataset_404(self):
        self.put_records([valid_well()])
        status, _ = call("get", "/api/file/v2/files/company-prod:master-data--Well:WT-1")
        self.assertEqual(status, 404)


class TestEntitlements(ServerTestCase):

    GROUP = "data.demo.viewers"

    def test_v2_groups_lists_seeded(self):
        status, payload = call("get", "/api/entitlements/v2/groups")
        self.assertEqual(status, 200)
        names = {g["name"] for g in payload["groups"]}
        self.assertIn("service.storage.viewer", names)
        self.assertIn("users.datalake.viewers", names)

    def test_create_group_and_members(self):
        email = f"{self.GROUP}@company-prod.company.com"
        status, payload = call("post", "/api/entitlements/v2/groups",
                               {"name": self.GROUP, "description": "demo viewers"})
        self.assertEqual(status, 201)
        self.assertEqual(payload["email"], email)
        status, _ = call("post", "/api/entitlements/v2/groups", {"name": self.GROUP})
        self.assertEqual(status, 409)

        status, _ = call("post", f"/api/entitlements/v2/groups/{email}/members",
                         {"email": "u1@acme.com", "role": "OWNER"})
        self.assertEqual(status, 200)
        call("post", f"/api/entitlements/v2/groups/{email}/members",
             {"email": "u2@acme.com", "role": "MEMBER"})

        status, payload = call("get", f"/api/entitlements/v2/groups/{email}/members")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["members"]), 2)
        _, payload = call("get", f"/api/entitlements/v2/groups/{email}/members?role=OWNER")
        self.assertEqual([m["email"] for m in payload["members"]], ["u1@acme.com"])
        _, payload = call("get", f"/api/entitlements/v2/groups/{email}/members?includeType=true")
        self.assertIn("type", payload["members"][0])

        status, payload = call("get", "/api/entitlements/v2/members/u1@acme.com/groups")
        self.assertEqual(status, 200)
        self.assertEqual([g["email"] for g in payload["groups"]], [email])

        status, _ = call("delete", f"/api/entitlements/v2/groups/{email}/members/u1@acme.com")
        self.assertEqual(status, 204)
        status, _ = call("delete", f"/api/entitlements/v2/groups/{email}/members/u1@acme.com")
        self.assertEqual(status, 404)

    def test_patch_and_delete_group(self):
        email = f"{self.GROUP}@company-prod.company.com"
        call("post", "/api/entitlements/v2/groups", {"name": self.GROUP})
        status, _ = call("patch", f"/api/entitlements/v2/groups/{email}",
                         {"description": "updated"})
        self.assertEqual(status, 204)
        status, _ = call("delete", f"/api/entitlements/v2/groups/{email}")
        self.assertEqual(status, 204)
        status, _ = call("get", f"/api/entitlements/v2/groups/{email}/members")
        self.assertEqual(status, 404)

    def test_group_rbac(self):
        saved = server.GROUPS_BY_TOKEN[server.TOKEN]
        server.GROUPS_BY_TOKEN[server.TOKEN] = {"users.datalake.viewers"}
        try:
            status, _ = call("post", "/api/entitlements/v2/groups", {"name": self.GROUP})
            self.assertEqual(status, 403)
        finally:
            server.GROUPS_BY_TOKEN[server.TOKEN] = saved


class TestStorageVersionsPatchCopy(ServerTestCase):

    RID = "company-prod:master-data--Well:WT-V1"

    def test_versions_patch_copy_query(self):
        self.put_records([valid_well(self.RID)])
        self.put_records([valid_well(self.RID)])

        status, payload = call("get", f"/api/storage/v2/records/versions/{self.RID}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordId"], self.RID)
        self.assertEqual(payload["versions"], [1, 2])

        _, rec = call("get", f"/api/storage/v2/records/{self.RID}?version=1")
        self.assertEqual(rec["version"], 1)
        _, rec = call("get", f"/api/storage/v2/records/{self.RID}/2")
        self.assertEqual(rec["version"], 2)
        status, _ = call("get", f"/api/storage/v2/records/{self.RID}/99")
        self.assertEqual(status, 404)

        status, payload = call("patch", f"/api/storage/v2/records/{self.RID}",
                               {"data": {"Status": "PX"}, "tags": {"stage": "demo"}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordIds"], [self.RID])
        _, rec = call("get", f"/api/storage/v2/records/{self.RID}")
        self.assertEqual(rec["version"], 3)
        self.assertEqual(rec["data"]["Status"], "PX")

        status, payload = call("patch", "/api/storage/v2/records",
                               [{"id": self.RID, "data": {"Status": "PD"}}])
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordCount"], 1)
        _, rec = call("get", f"/api/storage/v2/records/{self.RID}")
        self.assertEqual(rec["data"]["Status"], "PD")

        status, payload = call("post", f"/api/records/{self.RID}:copy", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["recordCount"], 1)
        new_id = payload["newRecordId"]
        self.assertNotEqual(new_id, self.RID)
        _, rec = call("get", f"/api/storage/v2/records/{new_id}")
        self.assertEqual(rec["data"]["FacilityName"], "EAGLE-01")
        self.assertEqual(rec["version"], 1)

        _, payload = call("get", f"/api/storage/v2/query/records?kind={server.WELL_KIND}")
        self.assertIn(self.RID, payload["records"])
        self.assertIn(new_id, payload["records"])

        _, rec = call("get", f"/api/storage/v2/records/{self.RID}?attribute=data.FacilityName")
        self.assertIn("FacilityName", rec["data"])
        self.assertNotIn("Status", rec["data"])

    def test_batch_and_single_delete_and_purge(self):
        rec_a = valid_well("company-prod:master-data--Well:WT-A")
        rec_b = valid_well("company-prod:master-data--Well:WT-B")
        rec_c = valid_well("company-prod:master-data--Well:WT-C")
        self.put_records([rec_a, rec_b, rec_c])

        status, _ = call("post", "/api/storage/v2/records/delete",
                         {"records": [rec_a["id"], rec_b["id"]]})
        self.assertEqual(status, 204)
        _, payload = call("get", "/api/storage/v2/records?deleted=true")
        self.assertEqual({r["id"] for r in payload["records"]}, {rec_a["id"], rec_b["id"]})
        _, payload = call("get", "/api/storage/v2/records")
        self.assertEqual([r["id"] for r in payload["records"]], [rec_c["id"]])

        status, _ = call("post", f"/api/records/{rec_c['id']}:delete", {})
        self.assertEqual(status, 204)
        _, payload = call("get", "/api/storage/v2/records?deleted=true")
        self.assertEqual({r["id"] for r in payload["records"]},
                         {rec_a["id"], rec_b["id"], rec_c["id"]})

        status, _ = call("delete", f"/api/storage/v2/records/versions/{rec_a['id']}")
        self.assertEqual(status, 204)
        _, payload = call("get", "/api/storage/v2/records?deleted=true")
        self.assertNotIn(rec_a["id"], {r["id"] for r in payload["records"]})
        status, _ = call("get", f"/api/storage/v2/records/versions/{rec_a['id']}")
        self.assertEqual(status, 404)


class TestSearchCursorAndHealth(ServerTestCase):

    def test_cursor_delete_and_liveness(self):
        for i in range(3):
            self.put_records([valid_well(f"company-prod:master-data--Well:WT-{i}")])
        wait_until(lambda: call("post", "/api/search/v2/query",
                                {"kind": server.WELL_KIND})[1]["totalCount"] == 3)

        _, payload = call("post", "/api/search/v2/query_with_cursor",
                          {"kind": server.WELL_KIND, "limit": 1})
        self.assertEqual(len(payload["results"]), 1)
        cursor = payload["cursor"]
        self.assertTrue(cursor)

        status, _ = call("delete", f"/api/search/v2/query_with_cursor/{cursor}")
        self.assertEqual(status, 204)
        status, _ = call("post", "/api/search/v2/query_with_cursor", {"cursor": cursor})
        self.assertEqual(status, 400)

        status, payload = call("get", "/api/search/v2/liveness_check")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "UP")
        status, payload = call("get", "/api/search/v2/readiness_check")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "UP")


class TestLegalQuery(ServerTestCase):

    def seed_tags(self):
        tags = {
            "company-prod-q1": {
                "description": "North Sea well operations",
                "properties": {"contractId": "C-100", "originator": "acme",
                               "expirationDate": "2027-06-15"},
            },
            "company-prod-q2": {
                "description": "south field development",
                "properties": {"contractId": "C-200", "originator": "acme",
                               "expirationDate": "2028-06-15"},
            },
            "company-prod-q3": {
                "description": "old archive",
                "properties": {"contractId": "C-900", "originator": "other",
                               "expirationDate": "2000-01-01"},
            },
        }
        for name, tag in tags.items():
            call("post", "/api/legal/v1/legaltags", {"name": name, **tag})

    def names(self, payload):
        return [t["name"] for t in payload["legalTags"]]

    def test_query_operators(self):
        self.seed_tags()
        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["any=acme"], "operatorList": ["union"]})
        self.assertEqual(self.names(payload), ["company-prod-q1", "company-prod-q2"])

        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["any=acme", "any=north"],
                           "operatorList": ["intersection"]})
        self.assertEqual(self.names(payload), ["company-prod-q1"])

        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["any=acme", "any=acme"], "operatorList": ["add"]})
        self.assertEqual(len(payload["legalTags"]), 4)

    def test_query_attr_between_and_valid(self):
        self.seed_tags()
        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["contractId=C-200"], "operatorList": ["union"]})
        self.assertEqual(self.names(payload), ["company-prod-q2"])

        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["expirationDate between (2022-01-01, 2029-12-31)"],
                           "operatorList": ["union"]})
        self.assertEqual(self.names(payload), ["company-prod-q1", "company-prod-q2"])

        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["any=acme"], "operatorList": ["union"],
                           "valid": False})
        self.assertEqual(self.names(payload), [])

    def test_query_valid_false_catches_expired(self):
        call("post", "/api/legal/v1/legaltags",
             {"name": "company-prod-q3", "properties": {"expirationDate": "2000-01-01"}})
        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["any=q3"], "operatorList": ["union"]})
        self.assertEqual(self.names(payload), [])
        _, payload = call("post", "/api/legal/v1/legaltags:query",
                          {"queryList": ["any=q3"], "operatorList": ["union"],
                           "valid": False})
        self.assertEqual(self.names(payload), ["company-prod-q3"])


class TestDatasetBatchEndpoints(ServerTestCase):

    DID = "company-prod:dataset--File.Generic:UP-2-LAS"

    def upload(self):
        _, instructions = call("post", "/api/dataset/v1/storageInstructions", {})
        token = instructions["storageLocation"]["signedUrl"].rsplit("/", 1)[1]
        body = b"D1 batch curve data"
        status, _ = call("put", f"/upload/{token}", body=body, raw=True,
                         token="", headers={"x-file-name": "f-2.las"})
        self.assertEqual(status, 200)

    def register(self):
        ds = valid_well(self.DID)
        ds["kind"] = server.DATASET_KIND
        ds["data"] = {"Name": "f-2.las", "FileName": "f-2.las", "Format": "LAS"}
        status, payload = call("put", "/api/dataset/v1/registerDataset",
                               {"datasetRegistries": [ds]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["datasetRegistries"][0]["id"], self.DID)

    def test_batch_register_and_get(self):
        self.upload()
        self.register()
        status, payload = call("post", "/api/dataset/v1/getDatasetRegistry",
                               {"datasetRegistryIds": [self.DID]})
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["datasetRegistries"]), 1)
        self.assertEqual(payload["datasetRegistries"][0]["id"], self.DID)

        status, payload = call("get", f"/api/dataset/v1/getDatasetRegistry/?id={self.DID}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["datasetRegistries"][0]["id"], self.DID)

        status, payload = call("get", f"/api/dataset/v1/retrievalInstructions?id={self.DID}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["datasets"][0]["datasetRegistryId"], self.DID)

    def test_revoke_and_soft_delete(self):
        self.upload()
        self.register()
        status, _ = call("post", "/api/dataset/v1/revokeURL",
                         {"datasetRegistryIds": [self.DID]})
        self.assertEqual(status, 200)
        _, payload = call("get", f"/api/dataset/v1/retrievalInstructions?id={self.DID}")
        self.assertEqual(payload["datasets"], [])

        status, _ = call("post", f"/api/dataset/v1/metadataRecord/{self.DID}/softDelete", {})
        self.assertEqual(status, 204)
        status, _ = call("get", f"/api/dataset/v1/getDatasetRegistry/?id={self.DID}")
        self.assertEqual(status, 404)


class TestFileV2Extended(ServerTestCase):

    DID = "company-prod:dataset--File.Generic:F-M1"

    def upload(self):
        _, instructions = call("post", "/api/dataset/v1/storageInstructions", {})
        token = instructions["storageLocation"]["signedUrl"].rsplit("/", 1)[1]
        return call("put", f"/upload/{token}", body=b"F2 metadata contents", raw=True,
                    token="", headers={"x-file-name": "f-3.las"})

    def test_upload_url_metadata_delivery(self):
        self.assertEqual(self.upload()[0], 200)
        status, payload = call("get", "/api/file/v2/files/uploadURL")
        self.assertEqual(status, 200)
        self.assertIn("FileSource", payload["Location"])
        self.assertIn("SignedUrl", payload["Location"])

        status, payload = call("post", "/api/file/v2/files/metadata", {
            "id": self.DID,
            "kindId": server.DATASET_KIND,
            "FileName": "f-3.las",
            "data": {"FileSource": "f-3.las"},
        })
        self.assertEqual(status, 201)
        self.assertEqual(payload["id"], self.DID)

        status, payload = call("get", f"/api/file/v2/files/{self.DID}/metadata")
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], self.DID)

        status, payload = call("get", f"/api/file/v2/files/{self.DID}/DownloadURL")
        self.assertEqual(status, 200)
        self.assertIn("signedURL", payload)
        self.assertEqual(payload["signedURL"].endswith("/files/f-3.las"), True)

        status, payload = call("post", "/api/file/v2/getLocation", {"FileID": self.DID})
        self.assertEqual(status, 200)
        self.assertEqual(payload["fileID"], self.DID)
        self.assertIn("SignedURL", payload["Location"])

        status, payload = call("post", "/api/file/v2/delivery/getFileSignedUrl",
                               {"srn": [self.DID]})
        self.assertEqual(status, 200)
        self.assertIn(self.DID, payload["processed"])
        self.assertEqual(payload["unprocessed"], [])

        _, payload = call("post", "/api/file/v2/delivery/getFileSignedUrl",
                          {"srn": ["missing-file-1", self.DID]})
        self.assertIn("missing-file-1", payload["unprocessed"])
        self.assertIn(self.DID, payload["processed"])

        status, _ = call("delete", f"/api/file/v2/files/{self.DID}/metadata")
        self.assertEqual(status, 204)
        status, _ = call("get", f"/api/file/v2/files/{self.DID}/DownloadURL")
        self.assertEqual(status, 404)

    def test_metadata_requires_file(self):
        status, _ = call("post", "/api/file/v2/files/metadata",
                         {"id": self.DID, "data": {"FileSource": "never-uploaded.bin"}})
        self.assertEqual(status, 400)


class TestWorkflowDeployment(ServerTestCase):

    def test_deploy_list_run_update_delete(self):
        status, payload = call("post", "/api/workflow/v1/workflow",
                               {"workflowName": "Demo_workflow", "description": "demo run"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["workflowName"], "Demo_workflow")

        _, payload = call("get", "/api/workflow/v1/workflow")
        names = [w["workflowName"] for w in payload]
        self.assertIn("Osdu_ingest", names)
        self.assertIn("Demo_workflow", names)

        status, _ = call("post", "/api/workflow/v1/workflow",
                         {"workflowName": "Demo_workflow", "description": "v2"})
        self.assertEqual(status, 200)
        _, payload = call("get", "/api/workflow/v1/workflow/Demo_workflow")
        self.assertEqual(payload["description"], "v2")

        status, payload = call("post", "/api/workflow/v1/workflow/Demo_workflow/workflowRun", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "submitted")
        run_id = payload["workflowRunId"]
        wait_until(lambda: call("get", f"/api/workflow/v1/workflow/Demo_workflow/workflowRun/{run_id}")[1]["status"] == "succeeded")

        _, payload = call("get", "/api/workflow/v1/workflow/Demo_workflow/workflowRun")
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["workflowRunId"], run_id)

        status, payload = call("put", f"/api/workflow/v1/workflow/Demo_workflow/workflowRun/{run_id}",
                               {"status": "cancelled"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "cancelled")

        status, _ = call("delete", f"/api/workflow/v1/workflow/Demo_workflow/workflowRun/{run_id}")
        self.assertEqual(status, 204)
        status, _ = call("delete", "/api/workflow/v1/workflow/Demo_workflow")
        self.assertEqual(status, 204)
        status, _ = call("get", "/api/workflow/v1/workflow/Demo_workflow")
        self.assertEqual(status, 404)

    def test_unknown_workflow_run_404(self):
        status, _ = call("post", "/api/workflow/v1/workflow/Nope/workflowRun", {})
        self.assertEqual(status, 404)


class TestSchemaService(ServerTestCase):

    CUSTOM = "osdu:wks:master-data--DemoCustom:1.0.0"

    def test_list_and_execute(self):
        status, payload = call("get", "/api/schema-service/v1/schema")
        self.assertEqual(status, 200)
        self.assertTrue(any(s.get("kind") == server.WELL_KIND for s in payload))
        self.assertTrue(any(s.get("kind") == server.DATASET_KIND for s in payload))

        status, _ = call("get", f"/api/schema-service/v1/schema/{server.WELL_KIND}")
        self.assertEqual(status, 200)

        body = {
            "kind": self.CUSTOM,
            "schemaInfo": {
                "schemaIdentity": {
                    "authority": "osdu", "source": "wks",
                    "entityType": "master-data--DemoCustom",
                    "schemaVersionMajor": 1, "schemaVersionMinor": 0,
                }
            },
            "schema": {"type": "object", "properties": {}},
        }
        status, payload = call("post", "/api/schema-service/v1/schema", body)
        self.assertEqual(status, 201)
        _, payload = call("get", f"/api/schema-service/v1/schema/{self.CUSTOM}")
        self.assertEqual(payload["kind"], self.CUSTOM)

        status, payload = call("put", "/api/schema-service/v1/schema", body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], self.CUSTOM)

        status, _ = call("put", "/api/schema-service/v1/schemas/system",
                         {"schema": {"title": "system", "type": "object"}})
        self.assertEqual(status, 200)

        status, _ = call("get", f"/api/schema-service/v1/schema/{self.CUSTOM}?x=1")
        self.assertEqual(status, 200)


class TestEntitlementsExtended(ServerTestCase):

    GROUP = "data.demo.extended"
    EMAIL = f"{GROUP}@company-prod.company.com"

    def setUp(self):
        super().setUp()
        call("post", "/api/entitlements/v2/groups", {"name": self.GROUP})
        call("post", f"/api/entitlements/v2/groups/{self.EMAIL}/members",
             {"email": "u1@acme.com", "role": "OWNER"})
        call("post", f"/api/entitlements/v2/groups/{self.EMAIL}/members",
             {"email": "u2@acme.com", "role": "MEMBER"})

    def test_members_count_with_role_filter(self):
        _, payload = call("get", f"/api/entitlements/v2/groups/{self.EMAIL}/membersCount")
        self.assertEqual(payload["count"], 2)
        _, payload = call("get", f"/api/entitlements/v2/groups/{self.EMAIL}/membersCount?role=owner")
        self.assertEqual(payload["count"], 1)

    def test_v1_role_required(self):
        _, payload = call("get", "/api/entitlements/v1/groups?roleRequired=true")
        for group in payload["groups"]:
            self.assertIn("role", group)
            self.assertIn("email", group)

    def test_app_ids_patch_and_create(self):
        status, payload = call("patch", f"/api/entitlements/v2/groups/{self.EMAIL}",
                               {"op": "replace", "path": "/appIds", "value": ["app-a", "app-b"]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["appIds"], ["app-a", "app-b"])

        status, _ = call("post", "/api/entitlements/v2/groups",
                         {"name": "data.demo.apps", "appIds": ["app-x"]})
        self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main()