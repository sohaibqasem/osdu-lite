#!/usr/bin/env python3
import csv
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import threading
import time
import uuid
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
FILES_DIR = Path(os.getenv("FILES_DIR", ROOT / "files"))
PORT = int(os.getenv("PORT", "8089"))
PARTITION = os.getenv("OSDU_PARTITION", "company-prod")
TOKEN = os.getenv("OSDU_TOKEN", "demo-token")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() not in {"0", "false", "no"}
PRELOAD_FULL_SAMPLE = os.getenv("PRELOAD_FULL_SAMPLE", "true").lower() not in {"0", "false", "no"}
INDEX_DELAY_SECONDS = float(os.getenv("INDEX_DELAY_SECONDS", "0.7"))
UPLOAD_TOKEN_TTL = float(os.getenv("UPLOAD_TOKEN_TTL", "3600"))
CURSOR_TTL = float(os.getenv("CURSOR_TTL", "300"))

WELL_KIND = "osdu:wks:master-data--Well:1.4.0"
WELLBORE_KIND = "osdu:wks:master-data--Wellbore:1.4.0"
WELLLOG_KIND = "osdu:wks:work-product-component--WellLog:1.4.0"
DATASET_KIND = "osdu:wks:dataset--File.Generic:1.0.0"
SUPPORTED_KINDS = [DATASET_KIND, WELL_KIND, WELLBORE_KIND, WELLLOG_KIND]
LEGAL_TAG = f"{PARTITION}-demo-legaltag"
ACL_VIEWERS = f"data.well360.viewers@{PARTITION}.company.com"
ACL_OWNERS = f"data.well360.owners@{PARTITION}.company.com"
DEFAULT_ACL = {"viewers": [ACL_VIEWERS], "owners": [ACL_OWNERS]}

GROUPS_ALL = {
    ACL_VIEWERS, ACL_OWNERS,
    "users.datalake.viewers", "users.datalake.editors",
    "service.search.user", "service.storage.viewer", "service.storage.editor",
    "service.legal.user",
    "service.entitlements.user", "service.entitlements.admin",
}
GROUPS_BY_TOKEN = {TOKEN: set(GROUPS_ALL)} if TOKEN else {}

STORE_LOCK = threading.RLock()
RECORD_BY_ID = {}
SEARCH_BY_ID = {}
UPLOAD_TOKENS = {}
FILE_META = {}
CURSORS = {}
CURSOR_LOCK = threading.Lock()
WORKFLOW_RUNS = {}
WORKFLOW_LOCK = threading.Lock()
GROUPS_LOCK = threading.RLock()
DELETED = set()
RECORD_TS = {}
RECORD_VERSIONS = {}
REVOKED_URLS = set()
SCHEMAS = {}

def seed_workflow_defs():
    now = time.time()
    return {
        "Osdu_ingest": {
            "workflowId": "wf-osdu-ingest-0001",
            "workflowName": "Osdu_ingest",
            "description": "Manifest based ingestion workflow",
            "version": "1.0.0",
            "createdby": "osdu-lite",
            "creationTimestamp": now,
            "registrationInstructions": {"dagName": "osdu-default"},
        },
    }

WORKFLOW_DEFS = seed_workflow_defs()

def default_legal_tag(name=None):
    return {
        "name": name or LEGAL_TAG,
        "description": "Demo LegalTag for OSDU-Lite ingestion",
        "properties": {
            "countryOfOrigin": ["SA"],
            "contractId": "No Contract Related",
            "expirationDate": "2099-12-31",
            "dataType": "Public Domain Data",
            "originator": "OSDU-Lite",
            "securityClassification": "Public",
            "exportClassification": "EAR99",
            "personalData": "No Personal Data",
            "extensionProperties": {},
        },
    }

LEGAL_TAGS = {LEGAL_TAG: default_legal_tag()}

def group_email(name):
    if "@" in name:
        return name
    return f"{name}@{PARTITION}.company.com"

def seed_groups():
    groups = {}
    for name in GROUPS_ALL:
        email = group_email(name)
        groups[email] = {
            "name": name,
            "email": email,
            "description": f"{name} - entitlement group",
            "members": {},
            "appIds": [],
        }
    return groups

GROUPS = seed_groups()

DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)

def read_csv(name):
    with (DATA_DIR / name).open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def scan_file_meta():
    for p in FILES_DIR.iterdir():
        if p.is_file():
            data = p.read_bytes()
            FILE_META[p.name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

def well_id(legacy):
    return f"{PARTITION}:master-data--Well:{legacy}"

def wellbore_id(legacy):
    return f"{PARTITION}:master-data--Wellbore:{legacy}"

def welllog_id(legacy):
    return f"{PARTITION}:work-product-component--WellLog:{legacy}"

def dataset_id(log_id):
    return f"{PARTITION}:dataset--File.Generic:{log_id}-LAS"

def record_base(record_id, kind, legacy_id, source):
    return {
        "id": record_id,
        "kind": kind,
        "version": 1,
        "acl": {"viewers": [ACL_VIEWERS], "owners": [ACL_OWNERS]},
        "legal": {"legaltags": [LEGAL_TAG], "otherRelevantDataCountries": ["SA"]},
        "tags": {"source-system": source, "legacy-id": legacy_id},
    }

def build_full_sample_records():
    records = []

    for row in read_csv("wells.csv"):
        rid = well_id(row["LEGACY_WELL_ID"])
        r = record_base(rid, WELL_KIND, row["LEGACY_WELL_ID"], "legacy-well-db")
        r["tags"]["field"] = row["FIELD_NAME"]
        r["data"] = {
            "FacilityName": row["WELL_NAME"],
            "FieldName": row["FIELD_NAME"],
            "Operator": row["OPERATOR"],
            "Country": row["COUNTRY"],
            "SpudDate": row["SPUD_DATE"],
            "Status": row["STATUS"],
            "SpatialLocation": {
                "Wgs84Coordinates": {
                    "type": "GeometryCollection",
                    "geometries": [{
                        "type": "Point",
                        "coordinates": [float(row["LONGITUDE"]), float(row["LATITUDE"])],
                    }],
                }
            },
        }
        records.append(r)

    for row in read_csv("wellbores.csv"):
        rid = wellbore_id(row["WELLBORE_ID"])
        r = record_base(rid, WELLBORE_KIND, row["WELLBORE_ID"], "legacy-well-db")
        r["data"] = {
            "FacilityName": row["WELLBORE_NAME"],
            "WellID": well_id(row["LEGACY_WELL_ID"]),
            "MeasuredDepthM": float(row["MD_M"]),
            "TrueVerticalDepthM": float(row["TVD_M"]),
            "WellboreType": row["TYPE"],
            "Status": row["STATUS"],
        }
        records.append(r)

    datasets = {}
    for row in read_csv("well_logs.csv"):
        did = dataset_id(row["LOG_ID"])
        ds = record_base(did, DATASET_KIND, f"{row['LOG_ID']}-LAS", "legacy-log-catalog")
        ds["tags"]["file-format"] = row["FORMAT"]
        ds["data"] = {
            "Name": row["FILE_NAME"],
            "FileName": row["FILE_NAME"],
            "Format": row["FORMAT"],
            "LegacyPath": row["LEGACY_PATH"],
        }
        datasets[did] = ds

        rid = welllog_id(row["LOG_ID"])
        r = record_base(rid, WELLLOG_KIND, row["LOG_ID"], "legacy-log-catalog")
        r["tags"]["file-format"] = row["FORMAT"]
        r["data"] = {
            "Name": f"{row['WELLBORE_ID']} {row['CURVES']}",
            "WellboreID": wellbore_id(row["WELLBORE_ID"]),
            "Datasets": [did],
            "Curves": [x.strip() for x in row["CURVES"].split("/")],
            "StartM": float(row["START_M"]),
            "StopM": float(row["STOP_M"]),
            "FileName": row["FILE_NAME"],
        }
        records.append(r)

    records.extend(datasets.values())
    return records

FULL_SAMPLE = build_full_sample_records()

def clear_state():
    with STORE_LOCK:
        RECORD_BY_ID.clear()
        SEARCH_BY_ID.clear()
        UPLOAD_TOKENS.clear()
        RECORD_VERSIONS.clear()
    with CURSOR_LOCK:
        CURSORS.clear()
    with WORKFLOW_LOCK:
        WORKFLOW_RUNS.clear()
        WORKFLOW_DEFS.clear()
        WORKFLOW_DEFS.update(seed_workflow_defs())
    with GROUPS_LOCK:
        GROUPS.clear()
        GROUPS.update(seed_groups())
    LEGAL_TAGS.clear()
    LEGAL_TAGS[LEGAL_TAG] = default_legal_tag()
    DELETED.clear()
    REVOKED_URLS.clear()
    SCHEMAS.clear()
    RECORD_TS.clear()

def iso_to_epoch(value):
    if value is None:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None

def legal_tag_raw(name):
    return LEGAL_TAGS.get(name)

LEGAL_TAG_PROPERTIES = [
    {"propertyName": "countryOfOrigin", "allowedValues": ["SA", "US", "GB", "NO"]},
    {"propertyName": "contractId", "allowedValues": ["Unknown", "No Contract Related", "No Contract"]},
    {"propertyName": "dataType",
     "allowedValues": ["Public Domain Data", "Private Data", "Commercially Seismic", "Other Proprietary"]},
    {"propertyName": "expirationDate", "allowedValues": None},
    {"propertyName": "originator", "allowedValues": None},
    {"propertyName": "securityClassification",
     "allowedValues": ["Public", "Restricted", "Confidential", "Secret"]},
    {"propertyName": "exportClassification", "allowedValues": ["EAR99"]},
    {"propertyName": "personalData", "allowedValues": ["No Personal Data", "Personal Data"]},
    {"propertyName": "extensionProperties", "allowedValues": None},
]

def is_legal_tag_valid(name):
    tag = LEGAL_TAGS.get(name)
    if tag is None:
        return False
    expiry = (tag.get("properties") or {}).get("expirationDate")
    if not expiry:
        return True
    try:
        exp = _dt.datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date()
        return exp >= _dt.datetime.now(_dt.timezone.utc).date()
    except (ValueError, TypeError):
        return True

def group_for_caller(handler):
    auth = handler.headers.get("Authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
    return GROUPS_BY_TOKEN.get(token, set())

def validate_record(rec):
    rid = rec.get("id")
    kind = rec.get("kind")
    if not rid or not kind:
        raise ValueError("Each record requires id and kind")
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"Unsupported kind: {kind}")
    acl = rec.get("acl")
    if not isinstance(acl, dict) or not (acl.get("viewers") or acl.get("owners")):
        raise ValueError(f"Record {rid}: missing acl.viewers / acl.owners")
    legal = rec.get("legal")
    if not isinstance(legal, dict) or not legal.get("legaltags"):
        raise ValueError(f"Record {rid}: missing legal.legaltags")
    for tag in legal["legaltags"]:
        if tag not in LEGAL_TAGS:
            raise ValueError(f"Record {rid}: unknown LegalTag '{tag}' (registered: {sorted(LEGAL_TAGS)})")
        if not is_legal_tag_valid(tag):
            raise ValueError(f"Record {rid}: LegalTag '{tag}' is not valid (expired)")

def index_record_later(record):
    rid = record["id"]
    snapshot = deepcopy(record)
    def apply():
        time.sleep(INDEX_DELAY_SECONDS)
        with STORE_LOCK:
            # only index if the stored record is still exactly the one we indexed
            current = RECORD_BY_ID.get(rid)
            if current and current.get("version") == snapshot.get("version") and rid not in DELETED:
                SEARCH_BY_ID[rid] = snapshot
    threading.Thread(target=apply, daemon=True).start()

def upsert_records(records, index_immediately=False):
    out = []
    now = time.time()
    with STORE_LOCK:
        for incoming in records:
            rec = deepcopy(incoming)
            validate_record(rec)
            rid = rec["id"]
            old = RECORD_BY_ID.get(rid)
            version = int(old.get("version", 0)) + 1 if old else int(rec.get("version", 1) or 1)
            rec["version"] = version
            RECORD_BY_ID[rid] = rec
            RECORD_VERSIONS.setdefault(rid, []).append({
                "version": version,
                "record": deepcopy(rec),
                "timestamp": now,
            })
            ts = RECORD_TS.setdefault(rid, {})
            if "createTime" not in ts:
                ts["createTime"] = now
            ts["modify"] = now
            DELETED.discard(rid)
            out.append({"id": rid, "version": version})
            if index_immediately:
                SEARCH_BY_ID[rid] = deepcopy(rec)
            else:
                index_record_later(rec)
    return out

def soft_delete_records(record_ids):
    removed = []
    with STORE_LOCK:
        for rid in record_ids:
            if rid in RECORD_BY_ID:
                DELETED.add(rid)
                SEARCH_BY_ID.pop(rid, None)
                removed.append(rid)
    return removed

def record_mtime(rid):
    ts = RECORD_TS.get(rid)
    return ts.get("modify") if ts else None

def storage_records_snapshot(kind, deleted_only, modify_after_epoch, descending):
    with STORE_LOCK:
        records = list(RECORD_BY_ID.values())
    out = []
    for r in records:
        rid = r["id"]
        if deleted_only != (rid in DELETED):
            continue
        if kind and not kind_matches(kind, r.get("kind", "")):
            continue
        if modify_after_epoch is not None:
            mtime = record_mtime(rid)
            if mtime is None or mtime < modify_after_epoch:
                continue
        out.append(r)
    out.sort(key=lambda r: r["id"], reverse=descending)
    return out

def load_full_sample(index_immediately=True):
    clear_state()
    upsert_records(FULL_SAMPLE, index_immediately=index_immediately)

def record_counts():
    with STORE_LOCK:
        records = list(RECORD_BY_ID.values())
    return {
        "wells": sum(1 for r in records if r.get("kind") == WELL_KIND),
        "wellbores": sum(1 for r in records if r.get("kind") == WELLBORE_KIND),
        "welllogs": sum(1 for r in records if r.get("kind") == WELLLOG_KIND),
        "datasets": sum(1 for r in records if r.get("kind") == DATASET_KIND),
    }

def is_full_sample_loaded():
    expected_ids = {r["id"] for r in FULL_SAMPLE}
    with STORE_LOCK:
        current_ids = set(RECORD_BY_ID)
    return current_ids == expected_ids

def current_data_profile():
    counts = record_counts()
    total = sum(counts.values())
    if total == 0:
        return "empty"
    if is_full_sample_loaded():
        return "full-sample"
    if counts == {"wells": 1, "wellbores": 1, "welllogs": 1, "datasets": 1}:
        return "tutorial-or-manifest"
    return "custom"

def get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur

def set_path(obj, path, value):
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value

def project_record(record, fields):
    if not fields:
        return deepcopy(record)
    out = {}
    for f in fields:
        value = get_path(record, f)
        if value is not None:
            set_path(out, f, deepcopy(value))
    return out

def kind_matches(pattern, kind):
    if isinstance(pattern, list):
        return any(kind_matches(x, kind) for x in pattern)
    return fnmatch.fnmatch(kind.lower(), str(pattern).lower())

def _compare(a, b):
    try:
        return (float(a) > float(b)) - (float(a) < float(b))
    except (TypeError, ValueError):
        return (str(a) > str(b)) - (str(a) < str(b))

def _in_range(value, lo, hi):
    if lo is not None and _compare(value, lo) < 0:
        return False
    if hi is not None and _compare(value, hi) > 0:
        return False
    return True

class MatchStar:
    def __call__(self, record):
        return True

class FieldMatch:
    def __init__(self, field, expected):
        self.field = field
        self.expected = expected
    def __call__(self, record):
        actual = get_path(record, self.field)
        if isinstance(actual, list):
            return any(fnmatch.fnmatch(str(x), self.expected) for x in actual)
        if actual is None:
            return False
        return fnmatch.fnmatch(str(actual), self.expected)

class RangeMatch:
    def __init__(self, field, lo, hi):
        self.field = field
        self.lo = lo
        self.hi = hi
    def __call__(self, record):
        actual = get_path(record, self.field)
        vals = actual if isinstance(actual, list) else [actual]
        return any(_in_range(v, self.lo, self.hi) for v in vals if v is not None)

class SubstringMatch:
    def __init__(self, text):
        self.text = text
    def __call__(self, record):
        return self.text.lower() in json.dumps(record).lower()

class And:
    def __init__(self, left, right):
        self.left = left
        self.right = right
    def __call__(self, record):
        return self.left(record) and self.right(record)

class Or:
    def __init__(self, left, right):
        self.left = left
        self.right = right
    def __call__(self, record):
        return self.left(record) or self.right(record)

class Not:
    def __init__(self, child):
        self.child = child
    def __call__(self, record):
        return not self.child(record)

class QueryParser:
    def __init__(self, text):
        self.tokens = self._tokenize(text)
        self.pos = 0

    @staticmethod
    def _tokenize(text):
        out = []
        i = 0
        n = len(text)
        while i < n:
            c = text[i]
            if c.isspace():
                i += 1
                continue
            if c in "()[]:*":
                out.append(("PUNCT", c))
                i += 1
                continue
            if c == '"':
                j = text.find('"', i + 1)
                if j < 0:
                    raise ValueError("Unterminated quoted value")
                out.append(("QUOTED", text[i + 1:j]))
                i = j + 1
                continue
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()[]:*\"":
                j += 1
            out.append(("WORD", text[i:j]))
            i = j
        return out

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _is_word(self, name):
        tok = self.peek()
        return isinstance(tok, tuple) and tok[0] == "WORD" and tok[1].upper() == name

    def parse(self):
        if not self.tokens:
            return MatchStar()
        expr = self._parse_or()
        if self.pos != len(self.tokens):
            raise ValueError("Trailing tokens in query")
        return expr

    def _parse_or(self):
        left = self._parse_and()
        while self._is_word("OR"):
            self.pos += 1
            right = self._parse_and()
            left = Or(left, right)
        return left

    def _parse_and(self):
        left = self._parse_unary()
        while True:
            if self._is_word("OR"):
                break
            if self._is_word("AND"):
                self.pos += 1
                right = self._parse_unary()
                left = And(left, right)
            elif self._starts_term():
                right = self._parse_unary()
                left = And(left, right)
            else:
                break
        return left

    def _starts_term(self):
        tok = self.peek()
        if tok is None:
            return False
        if isinstance(tok, tuple):
            return tok[0] in ("WORD", "QUOTED")
        return tok[1] in "(*"

    def _parse_unary(self):
        if self._is_word("NOT"):
            self.pos += 1
            return Not(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self):
        tok = self.peek()
        if tok == ("PUNCT", "("):
            self.pos += 1
            expr = self._parse_or()
            if self.peek() != ("PUNCT", ")"):
                raise ValueError("Unbalanced parentheses")
            self.pos += 1
            return expr
        tok = self.next()
        if isinstance(tok, tuple) and tok[0] in ("WORD", "QUOTED"):
            field = tok[1]
            if self.peek() == ("PUNCT", ":"):
                self.pos += 1
                return self._parse_value(field)
            return SubstringMatch(field)
        raise ValueError(f"Unexpected token: {tok}")

    def next(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _parse_value(self, field):
        tok = self.peek()
        if tok == ("PUNCT", "["):
            self.pos += 1
            lo = self._next_bound()
            hi = None
            if self._is_word("TO"):
                self.pos += 1
                hi = self._next_bound()
            if self.peek() != ("PUNCT", "]"):
                raise ValueError("Unterminated range")
            self.pos += 1
            return RangeMatch(field, lo, hi)
        if tok == ("PUNCT", "*"):
            self.pos += 1
            return FieldMatch(field, "*")
        if isinstance(tok, tuple) and tok[0] in ("WORD", "QUOTED"):
            self.pos += 1
            return FieldMatch(field, tok[1])
        raise ValueError("Expected a value after ':'")

    def _next_bound(self):
        tok = self.peek()
        if tok == ("PUNCT", "*"):
            self.pos += 1
            return None
        if isinstance(tok, tuple) and tok[0] in ("WORD", "QUOTED"):
            self.pos += 1
            return tok[1]
        raise ValueError("Expected a range bound")

def matches_query(record, query):
    if not query:
        return True
    q = query.strip()
    if q == "*":
        return True
    try:
        return QueryParser(q).parse()(record)
    except Exception:
        return q.strip('"').lower() in json.dumps(record).lower()

def search_records(payload):
    kind = payload.get("kind")
    if not kind:
        return []
    query = payload.get("query")
    with STORE_LOCK:
        records = [r for r in SEARCH_BY_ID.values() if r["id"] not in DELETED]
    found = [r for r in records if kind_matches(kind, r.get("kind", "")) and matches_query(r, query)]
    found.sort(key=lambda r: r["id"])
    return found

def owner_filter(records, caller_groups):
    if not caller_groups:
        return records
    out = []
    for r in records:
        owners = (r.get("acl") or {}).get("owners") or []
        if any(g in caller_groups for g in owners):
            out.append(r)
    return out

def _field_values(records, field):
    values = []
    for r in records:
        v = get_path(r, field)
        if isinstance(v, list):
            values.extend(v)
        elif v is not None:
            values.append(v)
    return values

def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def aggregate_records(records, aggregate_by):
    out = []
    for agg in aggregate_by or []:
        if not isinstance(agg, dict):
            continue
        field = agg.get("field")
        agg_type = agg.get("aggregateByType", "count")
        entry = {"field": field, "aggregateByType": agg_type}
        if agg_type == "count":
            entry["Count"] = {"count": len(records)}
        elif agg_type == "filteredTerms":
            values = _field_values(records, field)
            counts = {}
            for v in values:
                key = str(v)
                counts[key] = counts.get(key, 0) + 1
            bucket_size = int(agg.get("bucketSize", 10) or 10)
            terms = [{"key": k, "count": c} for k, c in
                     sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:bucket_size]]
            entry["FilteredTerms"] = {
                "terms": terms,
                "termCount": len(counts),
                "totalCount": len(values),
            }
        elif agg_type == "histogram":
            interval = _num(agg.get("interval", 100))
            buckets = {}
            for v in _field_values(records, field):
                n = _num(v)
                if n is None:
                    continue
                start = int(n // interval * interval)
                buckets.setdefault(start, 0)
                buckets[start] += 1
            ordered = [{"start": s, "end": s + interval, "count": c}
                       for s, c in sorted(buckets.items())]
            entry["Histogram"] = {"buckets": ordered}
        else:
            entry["Count"] = {"count": len(records)}
        out.append(entry)
    return out

def dataset_file_name(dataset_registry_id):
    with STORE_LOCK:
        ds = RECORD_BY_ID.get(dataset_registry_id)
    if not ds:
        return None
    data = ds.get("data", {}) or {}
    name = data.get("FileName") or data.get("Name")
    if name:
        return name
    fsi = (data.get("DatasetProperties") or {}).get("FileSourceInfo") or {}
    src = fsi.get("FileSource") or data.get("FileSource")
    if src:
        return Path(str(src)).name
    return None

def record_version(rid, version):
    with STORE_LOCK:
        entries = RECORD_VERSIONS.get(rid, [])
        for entry in entries:
            if entry["version"] == version:
                return deepcopy(entry["record"])
    return None

def version_numbers(rid):
    with STORE_LOCK:
        entries = RECORD_VERSIONS.get(rid, [])
    return sorted(entry["version"] for entry in entries)

def patch_record(rid, payload):
    with STORE_LOCK:
        current = RECORD_BY_ID.get(rid)
        if not current:
            return None
        rec = deepcopy(current)
    for key in ("acl", "legal", "tags", "ancestry"):
        if key in payload and isinstance(payload[key], dict):
            rec[key] = deepcopy(payload[key])
    if isinstance(payload.get("data"), dict):
        merged = rec.setdefault("data", {})
        for key, value in payload["data"].items():
            merged[key] = deepcopy(value)
    return upsert_records([rec], index_immediately=True)

def copy_record(rid, acl=None):
    with STORE_LOCK:
        current = RECORD_BY_ID.get(rid)
        if not current or rid in DELETED:
            return None, None
        base = deepcopy(current)
        new_id = rid + "_copy"
        suffix = 1
        while new_id in RECORD_BY_ID:
            suffix += 1
            new_id = f"{rid}_copy_{suffix}"
    if acl:
        base["acl"] = deepcopy(acl)
    base["id"] = new_id
    base.pop("version", None)
    out = upsert_records([base], index_immediately=True)
    return new_id, out[0]

def _expiry_seconds(value):
    try:
        text = str(value or "1H").strip().upper()
        number = int(text[:-1])
        return number * {"M": 60, "H": 3600, "D": 86400}.get(text[-1:], 3600)
    except (ValueError, TypeError):
        return UPLOAD_TOKEN_TTL

def legal_tag_payload(name):
    tag = LEGAL_TAGS[name]
    payload = {
        "name": name,
        "description": tag.get("description"),
        "isValid": is_legal_tag_valid(name),
        "properties": deepcopy(tag.get("properties") or {}),
    }
    payload["properties"]["securityClassification"] = tag.get("classification", "Public")
    return payload

def _tag_value(tag, attr):
    props = tag.get("properties") or {}
    if attr in props:
        return props[attr]
    target = attr.lower()
    def walk(obj):
        if not isinstance(obj, dict):
            return _MISSING
        for key, value in obj.items():
            if str(key).lower() == target:
                return value
            if isinstance(value, dict):
                found = walk(value)
                if found is not _MISSING:
                    return found
        return _MISSING
    return walk(props.get("extensionProperties") or {})

_MISSING = object()

def _contains(value, needle):
    if isinstance(value, list):
        return any(_contains(v, needle) for v in value)
    if value is None:
        return False
    if isinstance(value, bool):
        value = "true" if value else "false"
    return needle.lower() in str(value).lower()

def _tag_between(value, lo, hi):
    values = value if isinstance(value, list) else [value]
    for v in values:
        if v is None:
            continue
        try:
            day = _dt.date.fromisoformat(str(v)[:10])
        except (ValueError, TypeError):
            continue
        if lo:
            try:
                lo_day = _dt.date.fromisoformat(lo[:10])
            except (ValueError, TypeError):
                lo_day = None
            if lo_day and day <= lo_day:
                continue
        if hi:
            try:
                hi_day = _dt.date.fromisoformat(hi[:10])
            except (ValueError, TypeError):
                hi_day = None
            if hi_day and day >= hi_day:
                continue
        return True
    return False

def _parse_between(clause):
    low = clause.lower()
    marker = low.find(" between (")
    if marker < 0:
        return None
    attr = clause[:marker].strip()
    rest = clause[marker + len(" between ("):]
    if ")" not in rest:
        return None
    parts = [x.strip().strip("'\"") for x in rest[:rest.index(")")].split(",")]
    if len(parts) != 2:
        return None
    return attr, parts[0], parts[1]

def _legal_clause_matches(tag, clause):
    clause = clause.strip()
    if not clause:
        return True
    between = _parse_between(clause)
    if between:
        attr, lo, hi = between
        value = _tag_value(tag, attr)
        return value is not _MISSING and _tag_between(value, lo, hi)
    if "=" in clause:
        attr, _, value = clause.partition("=")
        attr = attr.strip()
        value = value.strip().strip('"\'')
        if not attr or attr.lower() == "any":
            return _legal_free_text(tag, value)
        found = _tag_value(tag, attr)
        return found is not _MISSING and _contains(found, value)
    return _legal_free_text(tag, clause)

def _legal_free_text(tag, needle):
    props = tag.get("properties") or {}
    candidates = [
        tag.get("name"),
        tag.get("description"),
        props.get("contractId"),
        props.get("originator"),
        props.get("countryOfOrigin"),
        props.get("extensionProperties"),
    ]
    return any(_contains(v, needle) for v in candidates if v is not None)

def legal_tags_query(payload, valid_only):
    clauses = payload.get("queryList") or []
    if not clauses:
        return {"legalTags": []}
    operator = (payload.get("operatorList") or ["union"])[0]
    matched = []
    for tag in LEGAL_TAGS.values():
        if is_legal_tag_valid(tag.get("name")) != valid_only:
            continue
        matched.append((tag, [_legal_clause_matches(tag, c) for c in clauses]))
    chosen = []
    clause_count = len(clauses)
    for tag, flags in matched:
        hits = sum(1 for f in flags if f)
        if operator == "intersection" and hits == clause_count:
            chosen.append(legal_tag_payload(tag.get("name")))
        elif operator in ("add", "union") and hits > 0:
            repeats = hits if operator == "add" else 1
            chosen.extend([legal_tag_payload(tag.get("name"))] * repeats)
    return {"legalTags": chosen}

def schema_info_for(kind):
    with STORE_LOCK:
        if kind in SCHEMAS:
            return deepcopy(SCHEMAS[kind])
    if kind not in SUPPORTED_KINDS:
        return None
    parts = kind.split(":")
    version = parts[3] if len(parts) > 3 else "1.0.0"
    major, minor = (version.split(".") + ["0", "0"])[:2]
    try:
        major, minor = int(major), int(minor)
    except ValueError:
        major, minor = 1, 0
    return {
        "createdBy": "osdu-lite",
        "status": "PUBLISHED",
        "createdAt": "2023-01-01T00:00:00.000Z",
        "schemaInfo": {
            "createdBy": "osdu-lite",
            "schemaIdentity": {
                "authority": parts[0],
                "source": parts[1],
                "entityType": parts[2],
                "schemaVersionMajor": major,
                "schemaVersionMinor": minor,
            },
            "created": "2023-01-01T00:00:00.000Z",
            "status": "PUBLISHED",
        },
        "schema": {
            "title": kind,
            "type": "object",
            "properties": {},
        },
        "kind": kind,
    }

def dataset_registration_records(record_ids):
    out = []
    with STORE_LOCK:
        for rid in record_ids:
            rec = RECORD_BY_ID.get(rid)
            if rec and rid not in DELETED and kind_matches("osdu:wks:dataset--*", rec.get("kind", "")):
                out.append(deepcopy(rec))
    return out

def manifest_records(payload):
    ctx = payload.get("executionContext", {})
    manifest = ctx.get("manifest", {})
    acl = ctx.get("acl")
    legal = ctx.get("legal")
    items = []
    items.extend(manifest.get("MasterData", []) or [])
    items.extend(manifest.get("ReferenceData", []) or [])
    data = manifest.get("Data", {}) or {}
    items.extend(data.get("Datasets", []) or [])
    items.extend(data.get("WorkProductComponents", []) or [])
    out = []
    for item in items:
        r = deepcopy(item)
        if acl and "acl" not in r:
            r["acl"] = deepcopy(acl)
        if legal and "legal" not in r:
            r["legal"] = deepcopy(legal)
        out.append(r)
    return out

def sweep_temp_state():
    now = time.time()
    with STORE_LOCK:
        for token, info in list(UPLOAD_TOKENS.items()):
            if now - info.get("created", 0) > UPLOAD_TOKEN_TTL:
                del UPLOAD_TOKENS[token]
    with CURSOR_LOCK:
        for cursor, state in list(CURSORS.items()):
            if now - state.get("updated", 0) > CURSOR_TTL:
                del CURSORS[cursor]

scan_file_meta()

if PRELOAD_FULL_SAMPLE:
    load_full_sample(index_immediately=True)

class Handler(BaseHTTPRequestHandler):
    server_version = "OSDU-Well360/0.7"

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}")

    def _json(self, status, payload, headers=None):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        correlation_id = self.headers.get("correlation-id")
        if correlation_id:
            self.send_header("correlation-id", correlation_id)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._json(status, {"code": status, "reason": message, "message": message})

    def _no_content(self):
        self.send_response(204)
        self.end_headers()

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            self._error(400, "Invalid JSON body")
            return None

    def _is_public_path(self, path):
        return path in {"/health", "/info"} or path.startswith("/files/") or path.startswith("/upload/")

    def _check_auth(self, path):
        if not REQUIRE_AUTH or self._is_public_path(path):
            return True
        auth = self.headers.get("Authorization", "")
        partition = self.headers.get("data-partition-id")
        if auth != f"Bearer {TOKEN}":
            self._error(401, f"Use Authorization: Bearer {TOKEN}")
            return False
        if partition != PARTITION:
            self._error(400, f"Use data-partition-id: {PARTITION}")
            return False
        return True

    def _rbac(self, required):
        if not REQUIRE_AUTH or not required:
            return True
        auth = self.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
        groups = GROUPS_BY_TOKEN.get(token, set())
        if groups & required:
            return True
        self._error(403, "Insufficient entitlements, requires one of: " + ", ".join(sorted(required)))
        return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, data-partition-id, correlation-id, frame-of-reference")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not self._check_auth(path):
            return

        if path in {"/health", "/info"}:
            with STORE_LOCK:
                stored = len(RECORD_BY_ID)
                indexed = len(SEARCH_BY_ID)
            counts = record_counts()
            full_sample_loaded = is_full_sample_loaded()
            return self._json(200, {
                "service": "osdu-well360-local",
                "status": "UP",
                "version": "0.7",
                "partition": PARTITION,
                "storedRecords": stored,
                "indexedRecords": indexed,
                "indexDelaySeconds": INDEX_DELAY_SECONDS,
                "preloadFullSampleConfigured": PRELOAD_FULL_SAMPLE,
                "preloadedFullSample": full_sample_loaded,
                "currentDataProfile": current_data_profile(),
                "recordsByKind": counts,
            })

        if path in {"/api/entitlements/v1/groups", "/entitlements/v1/groups"}:
            role_required = query.get("roleRequired", ["false"])[0].lower() in {"1", "true", "yes"}
            with GROUPS_LOCK:
                names = sorted(g["name"] for g in GROUPS.values())
                groups = []
                for name in names:
                    group = GROUPS.get(group_email(name))
                    item = {
                        "name": name,
                        "email": group["email"] if group else group_email(name),
                        "description": group["description"] if group else "",
                    }
                    if role_required:
                        item["role"] = "OWNER"
                    groups.append(item)
            return self._json(200, {"groups": groups})

        if path in {"/api/entitlements/v2/groups", "/entitlements/v2/groups"}:
            if not self._rbac({"service.entitlements.user"}):
                return
            caller = group_for_caller(self)
            role_required = query.get("roleRequired", ["false"])[0].lower() in {"1", "true", "yes"}
            with GROUPS_LOCK:
                groups = sorted((
                    {
                        "name": g["name"],
                        "email": g["email"],
                        "description": g["description"],
                    }
                    for g in GROUPS.values()
                    if g["name"] in caller or g["email"] in caller
                ), key=lambda g: g["name"])
            if role_required:
                for g in groups:
                    g["role"] = "OWNER"
            return self._json(200, {
                "desId": TOKEN,
                "memberEmail": TOKEN,
                "groups": groups,
            })

        for prefix in ("/api/entitlements/v2/members/", "/entitlements/v2/members/"):
            if path.startswith(prefix) and path.endswith("/groups"):
                if not self._rbac({"service.entitlements.user", "service.entitlements.admin"}):
                    return
                email = unquote(path[len(prefix):-len("/groups")])
                with GROUPS_LOCK:
                    groups = sorted((
                        {
                            "name": g["name"],
                            "email": g["email"],
                            "description": g["description"],
                        }
                        for g in GROUPS.values() if email in g["members"]
                    ), key=lambda g: g["name"])
                return self._json(200, {"desId": email, "memberEmail": email, "groups": groups})

        for prefix in ("/api/entitlements/v2/groups/", "/entitlements/v2/groups/"):
            if not path.startswith(prefix):
                continue
            rest = unquote(path[len(prefix):])
            if rest.endswith("/membersCount"):
                if not self._rbac({"service.entitlements.user"}):
                    return
                group_email_value = rest[:-len("/membersCount")]
                role = query.get("role", [None])[0]
                with GROUPS_LOCK:
                    group = GROUPS.get(group_email_value)
                    if not group:
                        return self._error(404, f"Group not found: {group_email_value}")
                    members = {
                        m_email: m_role
                        for m_email, m_role in group["members"].items()
                        if not role or m_role.lower() == role.lower()
                    }
                return self._json(200, {"count": len(members)})
            if rest.endswith("/members"):
                if not self._rbac({"service.entitlements.user"}):
                    return
                group_email_value = rest[:-len("/members")]
                with GROUPS_LOCK:
                    group = GROUPS.get(group_email_value)
                    if not group:
                        return self._error(404, f"Group not found: {group_email_value}")
                    include_type = query.get("includeType", ["false"])[0].lower() in {"1", "true", "yes"}
                    role = query.get("role", [None])[0]
                    members = []
                    for m_email, m_role in sorted(group["members"].items()):
                        if role and m_role.lower() != role.lower():
                            continue
                        item = {"email": m_email, "role": m_role}
                        if include_type:
                            item["type"] = "USER"
                        members.append(item)
                return self._json(200, {"members": members})

        if path in {"/api/legal/v1/legaltags:properties", "/legal/v1/legaltags:properties"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            return self._json(200, {"legaltagProperties": LEGAL_TAG_PROPERTIES})

        if path in {"/api/legal/v1/legaltags", "/legal/v1/legaltags"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            valid_only = query.get("valid", ["true"])[0].lower() not in {"0", "false", "no"}
            tags = []
            for name in sorted(LEGAL_TAGS):
                if is_legal_tag_valid(name) != valid_only:
                    continue
                tags.append(legal_tag_payload(name))
            return self._json(200, {"legalTags": tags})

        for prefix in ("/api/legal/v1/legaltags/", "/legal/v1/legaltags/"):
            if path.startswith(prefix):
                name = unquote(path[len(prefix):])
                if name not in LEGAL_TAGS:
                    return self._error(404, f"LegalTag not found: {name}")
                return self._json(200, {**LEGAL_TAGS[name], "isValid": is_legal_tag_valid(name)})

        if path in {"/api/storage/v2/query/records", "/storage/v2/query/records"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            kind = query.get("kind", [None])[0]
            limit = min(max(int(query.get("limit", [10])[0]), 1), 1000)
            matched = storage_records_snapshot(kind, False, None, False)
            record_ids = [r["id"] for r in matched]
            page = record_ids[:limit]
            next_cursor = None
            if len(record_ids) > limit:
                next_cursor = "sr-" + uuid.uuid4().hex[:16]
                with CURSOR_LOCK:
                    CURSORS[next_cursor] = {
                        "results": [deepcopy(r) for r in matched],
                        "pos": limit,
                        "updated": time.time(),
                    }
            return self._json(200, {"records": page, "cursor": next_cursor})

        if path in {"/api/storage/v2/query/kinds", "/storage/v2/query/kinds"}:
            limit = min(max(int(query.get("limit", [100])[0]), 1), 100)
            return self._json(200, SUPPORTED_KINDS[:limit])

        if path in {"/api/storage/v2/records", "/storage/v2/records"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            kind = query.get("kind", [None])[0]
            try:
                limit = min(max(int(query.get("limit", [20])[0]), 1), 1000)
            except ValueError:
                limit = 20
            deleted_only = query.get("deleted", ["false"])[0].lower() in {"1", "true", "yes"}
            descending = query.get("sortOrder", ["asc"])[0].lower() == "desc"
            modify_epoch = iso_to_epoch(query.get("modifyAfterDate", [None])[0])
            cursor = query.get("cursor", [None])[0]
            if cursor:
                with CURSOR_LOCK:
                    state = CURSORS.get(cursor)
                    if not state:
                        return self._error(400, "Unknown/expired cursor")
                    results = state["results"]
                    pos = state["pos"]
                    page = [deepcopy(r) for r in results[pos:pos + limit]]
                    state["pos"] = pos + limit
                    state["updated"] = time.time()
                    next_cursor = cursor if state["pos"] < len(results) else None
                return self._json(200, {"records": page, "cursor": next_cursor})
            matched = storage_records_snapshot(kind, deleted_only, modify_epoch, descending)
            page = [deepcopy(r) for r in matched[:limit]]
            if len(matched) > limit:
                cursor = "sr-" + uuid.uuid4().hex[:16]
                with CURSOR_LOCK:
                    CURSORS[cursor] = {
                        "results": [deepcopy(r) for r in matched],
                        "pos": limit,
                        "updated": time.time(),
                    }
                    return self._json(200, {"records": page, "cursor": cursor})
            return self._json(200, {"records": page, "cursor": None})

        for prefix in ("/api/storage/v2/records/versions/", "/storage/v2/records/versions/"):
            if path.startswith(prefix):
                if not self._rbac({"service.storage.viewer"}):
                    return
                rid = unquote(path[len(prefix):])
                versions = version_numbers(rid)
                if not versions:
                    return self._error(404, f"Record not found: {rid}")
                return self._json(200, {"recordId": rid, "versions": versions})

        for prefix in ("/api/storage/v2/records/", "/storage/v2/records/"):
            if path.startswith(prefix):
                if not self._rbac({"service.storage.viewer"}):
                    return
                rest = unquote(path[len(prefix):])
                rid = rest
                if "/" in rest:
                    rid, version_text = rest.split("/", 1)
                    try:
                        version = int(version_text)
                    except ValueError:
                        return self._error(404, f"Record not found: {rid}")
                    rec = record_version(rid, version)
                    if not rec:
                        return self._error(404, f"Record version not found: {rid} v{version}")
                    return self._json(200, rec)
                with STORE_LOCK:
                    rec = RECORD_BY_ID.get(rid)
                    deleted = rid in DELETED
                if not rec or deleted:
                    return self._error(404, f"Record not found: {rid}")
                version_param = query.get("version", [None])[0]
                if version_param:
                    try:
                        stored = record_version(rid, int(version_param))
                    except ValueError:
                        stored = None
                    if not stored:
                        return self._error(404, f"Record version not found: {rid} v{version_param}")
                    rec = stored
                attributes = query.get("attribute", [None])
                if attributes and attributes != [None]:
                    rec = project_record(rec, attributes)
                return self._json(200, rec)

        if path in {"/api/schema-service/v1/schema", "/schema-service/v1/schema"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            infos = []
            for kind in SUPPORTED_KINDS:
                infos.append(schema_info_for(kind))
            with STORE_LOCK:
                for key, payload in sorted(SCHEMAS.items()):
                    infos.append(deepcopy(payload))
            return self._json(200, infos)

        for prefix in ("/api/schema-service/v1/schema/", "/schema-service/v1/schema/"):
            if path.startswith(prefix):
                kind = unquote(path[len(prefix):])
                info = schema_info_for(kind)
                if not info:
                    return self._error(404, f"Schema not found: {kind}")
                return self._json(200, info)

        if path in {"/api/dataset/v1/getDatasetRegistry/", "/dataset/v1/getDatasetRegistry/"}:
            if not self._rbac({"service.storage.viewer", "service.legal.user"}):
                return
            did = query.get("id", [None])[0]
            records = dataset_registration_records([did])
            if not records:
                return self._error(404, f"Dataset not found: {did}")
            return self._json(200, {"datasetRegistries": records})

        if path in {"/api/dataset/v1/retrievalInstructions", "/dataset/v1/retrievalInstructions"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            ids = query.get("id", [None])[0]
            items = []
            host = self.headers.get("Host", f"localhost:{PORT}")
            if ids:
                for did in [ids]:
                    if did in REVOKED_URLS:
                        continue
                    fname = dataset_file_name(did)
                    if not fname:
                        continue
                    props = {
                        "signedUrl": f"http://{host}/files/{fname}",
                        "expiresInSeconds": 3600,
                    }
                    meta = FILE_META.get(fname)
                    if meta:
                        props["size"] = meta["size"]
                        props["sha256"] = meta["sha256"]
                    items.append({
                        "datasetRegistryId": did,
                        "retrievalProperties": props,
                    })
            return self._json(200, {"datasets": items})

        if path in {"/api/file/v2/files/uploadURL", "/file/v2/files/uploadURL"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            token = uuid.uuid4().hex
            host = self.headers.get("Host", f"localhost:{PORT}")
            with STORE_LOCK:
                UPLOAD_TOKENS[token] = {"created": time.time(), "kindSubType": "dataset--File.Generic"}
            return self._json(200, {
                "fileID": f"file-{token[:12]}",
                "Location": {
                    "FileSource": f"uploaded-{token}.bin",
                    "SignedUrl": f"http://{host}/upload/{token}",
                    "expiresInSeconds": int(UPLOAD_TOKEN_TTL),
                },
            })

        for prefix in ("/api/file/v2/files/", "/file/v2/files/"):
            if not path.startswith(prefix):
                continue
            if not self._rbac({"service.storage.viewer", "service.legal.user"}):
                return
            rest = unquote(path[len(prefix):])
            host = self.headers.get("Host", f"localhost:{PORT}")
            if rest.endswith("/metadata"):
                fid = rest[:-len("/metadata")]
                with STORE_LOCK:
                    rec = RECORD_BY_ID.get(fid)
                    deleted = fid in DELETED
                if not rec or deleted or not kind_matches("osdu:wks:dataset--*", rec.get("kind", "")):
                    return self._error(404, f"File not found: {fid}")
                return self._json(200, deepcopy(rec))
            if rest.endswith("/downloadURL") or rest.endswith("/DownloadURL"):
                capital = rest.endswith("/DownloadURL")
                fid = rest[:-len("/downloadURL")] if not capital else rest[:-len("/DownloadURL")]
                with STORE_LOCK:
                    rec = RECORD_BY_ID.get(fid)
                    deleted = fid in DELETED
                if not rec or deleted or not kind_matches("osdu:wks:dataset--*", rec.get("kind", "")):
                    return self._error(404, f"File not found: {fid}")
                fname = dataset_file_name(fid)
                if not fname:
                    return self._error(404, f"File source missing for: {fid}")
                meta = FILE_META.get(fname, {})
                if capital:
                    return self._json(200, {"signedURL": f"http://{host}/files/{fname}"})
                body = {"signedUrl": f"http://{host}/files/{fname}", "expiresInSeconds": 3600}
                if meta:
                    body["size"] = meta["size"]
                    body["sha256"] = meta["sha256"]
                return self._json(200, body)
            with STORE_LOCK:
                rec = RECORD_BY_ID.get(rest)
                deleted = rest in DELETED
            if not rec or deleted or not kind_matches("osdu:wks:dataset--*", rec.get("kind", "")):
                return self._error(404, f"File not found: {rest}")
            fname = dataset_file_name(rest)
            meta = FILE_META.get(fname, {})
            location = {"expiresInSeconds": 3600}
            if fname:
                location["signedUrl"] = f"http://{host}/files/{fname}"
            if meta:
                location["size"] = meta["size"]
                location["sha256"] = meta["sha256"]
            ts = RECORD_TS.get(rest, {})
            init_time = _dt.datetime.fromtimestamp(
                ts.get("createTime", time.time()), tz=_dt.timezone.utc
            ).isoformat()
            return self._json(200, {
                "fileID": rest,
                "fileInitTime": init_time,
                "fileSourceDatasWithLocation": [
                    {"fileSource": fname or "", "location": location}
                ],
            })

        if path in {"/api/workflow/v1/workflow", "/workflow/v1/workflow"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            with WORKFLOW_LOCK:
                defs = [deepcopy(d) for d in sorted(WORKFLOW_DEFS.values(), key=lambda d: d["workflowName"])]
            return self._json(200, defs)

        for prefix in ("/api/workflow/v1/workflow/", "/workflow/v1/workflow/"):
            if path.startswith(prefix):
                rest = unquote(path[len(prefix):])
                segs = rest.split("/")
                if len(segs) == 3 and segs[1] == "workflowRun":
                    name, run_id = segs[0], segs[2]
                    if not self._rbac({"service.storage.viewer"}):
                        return
                    with WORKFLOW_LOCK:
                        run = WORKFLOW_RUNS.get(run_id)
                    if not run or run.get("workflowName") != name:
                        return self._error(404, f"Workflow run not found: {run_id}")
                    return self._json(200, {"workflowRunId": run_id, **run})
                if len(segs) == 2 and segs[1] == "workflowRun":
                    if not self._rbac({"service.storage.viewer"}):
                        return
                    name = segs[0]
                    with WORKFLOW_LOCK:
                        runs = [{"workflowRunId": rid, **deepcopy(r)}
                                for rid, r in sorted(WORKFLOW_RUNS.items())
                                if r.get("workflowName") == name]
                    return self._json(200, runs)
                if len(segs) == 1:
                    if not self._rbac({"service.storage.viewer"}):
                        return
                    name = segs[0]
                    if name in WORKFLOW_DEFS:
                        return self._json(200, deepcopy(WORKFLOW_DEFS[name]))
                    with WORKFLOW_LOCK:
                        run = WORKFLOW_RUNS.get(name)
                    if run:
                        return self._json(200, {"workflowRunId": name, **run})
                    return self._error(404, f"Workflow not found: {name}")
                return self._error(404, f"Unknown workflow endpoint: {path}")

        if path.startswith("/files/"):
            filename = Path(unquote(path[len("/files/"):])).name
            target = FILES_DIR / filename
            if not target.exists():
                return self._error(404, "File not found")
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", f'inline; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in {"/api/search/v2/liveness_check", "/search/v2/liveness_check",
                    "/api/search/v2/readiness_check", "/search/v2/readiness_check"}:
            return self._json(200, {"status": "UP"})

        self._error(404, f"Unknown endpoint: {path}")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not self._check_auth(path):
            return
        sweep_temp_state()
        payload = self._read_json()
        if payload is None:
            return

        if path in {"/api/search/v2/query", "/search/v2/query"}:
            if not self._rbac({"service.search.user"}):
                return
            found = search_records(payload)
            if payload.get("query_as_owner"):
                found = owner_filter(found, group_for_caller(self))
            offset = max(0, int(payload.get("offset", 0)))
            limit = max(1, min(1000, int(payload.get("limit", 10))))
            page = found[offset:offset + limit]
            fields = payload.get("returnedFields")
            response = {
                "results": [project_record(r, fields) for r in page],
            }
            if payload.get("trackTotalCount", True) is not False:
                response["totalCount"] = len(found)
            if payload.get("aggregateBy"):
                response["aggregates"] = aggregate_records(found, payload["aggregateBy"])
            return self._json(200, response)

        if path in {"/api/search/v2/query_with_cursor", "/search/v2/query_with_cursor"}:
            if not self._rbac({"service.search.user"}):
                return
            cursor = payload.get("cursor")
            if cursor:
                with CURSOR_LOCK:
                    state = CURSORS.get(cursor)
                    if not state:
                        return self._error(400, "Unknown/expired cursor")
                    found = state["results"]
                    pos = state["pos"]
                    limit = state["limit"]
                    fields = state["fields"]
                    page = found[pos:pos + limit]
                    state["pos"] = pos + limit
                    state["updated"] = time.time()
                    return self._json(200, {
                        "results": [project_record(r, fields) for r in page],
                        "totalCount": len(found),
                        "cursor": cursor,
                    })
            found = search_records(payload)
            limit = max(1, min(1000, int(payload.get("limit", 10))))
            fields = payload.get("returnedFields")
            cursor = "demo-" + uuid.uuid4().hex[:16]
            first = found[:limit]
            with CURSOR_LOCK:
                CURSORS[cursor] = {
                    "results": found, "pos": limit, "limit": limit,
                    "fields": fields, "updated": time.time()
                }
            return self._json(200, {
                "results": [project_record(r, fields) for r in first],
                "totalCount": len(found),
                "cursor": cursor,
            })

        if path in {
            "/api/storage/v2/query/records:batch", "/storage/v2/query/records:batch",
            "/api/storage/v2/query/records", "/storage/v2/query/records"
        }:
            if not self._rbac({"service.storage.viewer"}):
                return
            ids = payload.get("records", [])
            with STORE_LOCK:
                records = [deepcopy(RECORD_BY_ID[x]) for x in ids
                           if x in RECORD_BY_ID and x not in DELETED]
            return self._json(200, {"records": records})

        if path in {"/api/storage/v2/records/delete", "/storage/v2/records/delete"}:
            if not self._rbac({"users.datalake.editors"}):
                return
            ids = payload if isinstance(payload, list) else payload.get("records", [])
            if not isinstance(ids, list) or not ids:
                return self._error(400, "Expected a non-empty list of record ids")
            soft_delete_records(ids)
            return self._no_content()

        if path in {"/api/dataset/v1/storageInstructions", "/dataset/v1/storageInstructions",
                    "/api/dataset/v1/getStorageInstructions", "/dataset/v1/getStorageInstructions"}:
            if not self._rbac({"service.storage.editor"}):
                return
            kind_subtype = query.get("kindSubType", ["dataset--File.Generic"])[0]
            token = uuid.uuid4().hex
            host = self.headers.get("Host", f"localhost:{PORT}")
            with STORE_LOCK:
                UPLOAD_TOKENS[token] = {"created": time.time(), "kindSubType": kind_subtype}
            return self._json(200, {
                "storageLocation": {
                    "signedUrl": f"http://{host}/upload/{token}",
                    "expiresInSeconds": int(UPLOAD_TOKEN_TTL),
                },
                "kindSubType": kind_subtype,
            })

        if path in {"/api/dataset/v1/retrievalInstructions", "/dataset/v1/retrievalInstructions",
                    "/api/dataset/v1/getRetrievalInstructions", "/dataset/v1/getRetrievalInstructions"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            ids = payload.get("datasetRegistryIds") or payload.get("datasetRegistryId") or []
            if isinstance(ids, str):
                ids = [ids]
            host = self.headers.get("Host", f"localhost:{PORT}")
            items = []
            for did in ids:
                fname = dataset_file_name(did)
                if not fname:
                    continue
                props = {
                    "signedUrl": f"http://{host}/files/{fname}",
                    "expiresInSeconds": 3600,
                }
                meta = FILE_META.get(fname)
                if meta:
                    props["size"] = meta["size"]
                    props["sha256"] = meta["sha256"]
                items.append({
                    "datasetRegistryId": did,
                    "retrievalProperties": props,
                })
            return self._json(200, {"datasets": items})

        for prefix in ("/api/records/", "/records/"):
            if path.startswith(prefix):
                rest = unquote(path[len(prefix):])
                if rest.endswith(":copy"):
                    rid = rest[:-len(":copy")]
                    if not self._rbac({"service.storage.editor"}):
                        return
                    with STORE_LOCK:
                        exists = rid in RECORD_BY_ID and rid not in DELETED
                    if not exists:
                        return self._error(404, f"Record not found: {rid}")
                    acl = payload.get("acl") if isinstance(payload, dict) else None
                    new_id, entry = copy_record(rid, acl)
                    return self._json(200, {
                        "recordCount": 1,
                        "recordIds": [entry],
                        "skippedRecordIds": [],
                        "newRecordId": new_id,
                    })
                if rest.endswith(":delete"):
                    rid = rest[:-len(":delete")]
                    if not self._rbac({"users.datalake.editors"}):
                        return
                    with STORE_LOCK:
                        if rid not in RECORD_BY_ID:
                            return self._error(404, f"Record not found: {rid}")
                    soft_delete_records([rid])
                    return self._no_content()

        if path in {"/api/dataset/v1/getDatasetRegistry", "/dataset/v1/getDatasetRegistry"}:
            if not self._rbac({"service.storage.viewer"}):
                return
            ids = payload.get("datasetRegistryIds") or payload.get("datasetRegistryId") or []
            if isinstance(ids, str):
                ids = [ids]
            return self._json(200, {"datasetRegistries": dataset_registration_records(ids)})

        if path in {"/api/dataset/v1/revokeURL", "/dataset/v1/revokeURL"}:
            if not self._rbac({"service.storage.editor"}):
                return
            kind_subtype = query.get("kindSubType", [None])[0]
            target = payload.get("datasetRegistryIds") if isinstance(payload, dict) else payload
            target = target or payload
            if isinstance(target, str):
                target = [target]
            if isinstance(target, dict):
                REVOKED_URLS.update(k for k in target if target[k])
            elif isinstance(target, list):
                REVOKED_URLS.update(k for k in target if k)
            kind_text = f" kindSubType={kind_subtype}" if kind_subtype else ""
            return self._json(200, {
                "status": "revoked",
                "recordCount": len(REVOKED_URLS),
                "message": f"Revoked URLs{kind_text}",
            })

        for prefix in ("/api/dataset/v1/metadataRecord/", "/dataset/v1/metadataRecord/"):
            if path.startswith(prefix) and path.endswith("/softDelete"):
                if not self._rbac({"service.storage.editor"}):
                    return
                rid = unquote(path[len(prefix):-len("/softDelete")])
                with STORE_LOCK:
                    if rid not in RECORD_BY_ID:
                        return self._error(404, f"Record not found: {rid}")
                soft_delete_records([rid])
                return self._no_content()

        if path in {"/api/legal/v1/legaltags:validate", "/legal/v1/legaltags:validate"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            names = payload.get("legaltags") or payload.get("name") or []
            if isinstance(names, str):
                names = [names]
            invalid = []
            for tag_name in names:
                if not tag_name:
                    continue
                if is_legal_tag_valid(tag_name):
                    continue
                if tag_name in LEGAL_TAGS:
                    expiry = (LEGAL_TAGS[tag_name].get("properties") or {}).get("expirationDate")
                    reason = f"LegalTag not valid (expirationDate={expiry})"
                else:
                    reason = "LegalTag not found"
                invalid.append({"name": tag_name, "reason": reason})
            return self._json(200, {"invalidLegalTags": invalid})

        if path in {"/api/legal/v1/legaltags:batchRetrieve", "/legal/v1/legaltags:batchRetrieve"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            names = payload.get("legaltags") or payload.get("name") or []
            if isinstance(names, str):
                names = [names]
            tags = [LEGAL_TAGS[n] for n in names if n in LEGAL_TAGS]
            return self._json(200, {"legalTags": tags})

        if path in {"/api/legal/v1/legaltags:query", "/legal/v1/legaltags:query"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            valid_only = payload.get("valid", True)
            if isinstance(valid_only, str):
                valid_only = valid_only.lower() not in {"0", "false", "no"}
            return self._json(200, legal_tags_query(payload, bool(valid_only)))

        if path in {"/api/legal/v1/legaltags", "/legal/v1/legaltags"}:
            if not self._rbac({"users.datalake.editors"}):
                return
            name = payload.get("name")
            if not name or not isinstance(name, str):
                return self._error(400, "LegalTag name is required")
            if name in LEGAL_TAGS:
                return self._error(409, f"LegalTag already exists: {name}")
            props = payload.get("properties")
            tag = {
                "name": name,
                "description": payload.get("description", "LegalTag managed by OSDU-Lite"),
                "properties": deepcopy(props) if isinstance(props, dict)
                else deepcopy(default_legal_tag()["properties"]),
            }
            LEGAL_TAGS[name] = tag
            return self._json(201, {**tag, "isValid": is_legal_tag_valid(name)})

        if path in {"/api/entitlements/v2/groups", "/entitlements/v2/groups"}:
            if not self._rbac({"service.entitlements.admin"}):
                return
            name = payload.get("name")
            if not name or not isinstance(name, str) or not name:
                return self._error(400, "Group name is required")
            email = group_email(name)
            with GROUPS_LOCK:
                if email in GROUPS:
                    return self._error(409, f"Group already exists: {email}")
                GROUPS[email] = {
                    "name": name,
                    "email": email,
                    "description": payload.get("description", f"{name} - entitlement group"),
                    "members": {},
                    "appIds": list(payload.get("aPpIds") or payload.get("appIds") or []),
                }
                created = {
                    "name": name,
                    "email": email,
                    "description": GROUPS[email]["description"],
                    "appIds": GROUPS[email]["appIds"],
                }
            return self._json(201, created)

        for prefix in ("/api/entitlements/v2/groups/", "/entitlements/v2/groups/"):
            if not path.startswith(prefix):
                continue
            rest = unquote(path[len(prefix):])
            if rest.endswith("/members"):
                if not self._rbac({"service.entitlements.user", "service.entitlements.admin"}):
                    return
                group_email_value = rest[:-len("/members")]
                with GROUPS_LOCK:
                    group = GROUPS.get(group_email_value)
                    if not group:
                        return self._error(404, f"Group not found: {group_email_value}")
                    entries = payload.get("members", payload if isinstance(payload, list) else None)
                    if entries is None:
                        entries = [payload]
                    if isinstance(entries, dict):
                        entries = [entries]
                    added = []
                    for entry in entries:
                        if not isinstance(entry, dict) or not entry.get("email"):
                            return self._error(400, "Each member requires an 'email'")
                        m_role = str(entry.get("role", "MEMBER"))
                        if m_role.upper() not in {"OWNER", "MEMBER"}:
                            return self._error(400, "Member role must be OWNER or MEMBER")
                        group["members"][entry["email"]] = m_role.upper()
                        added.append({"email": entry["email"], "role": m_role.upper()})
                return self._json(200, {"members": added})

        if path in {"/api/file/v2/files/metadata", "/file/v2/files/metadata"}:
            if not self._rbac({"service.storage.editor"}):
                return
            metas = payload.get("fileRecord", payload)
            if isinstance(metas, dict) and any(k in metas for k in ("id", "kindId", "data")):
                metas = [metas]
            elif isinstance(metas, dict):
                metas = metas.get("records", [metas])
            created_ids = []
            for meta in metas:
                if not isinstance(meta, dict):
                    return self._error(400, "Each file metadata entry must be an object")
                rid = meta.get("id") or f"{PARTITION}:dataset--File.Generic:{uuid.uuid4().hex[:12]}"
                kind = meta.get("kindId") or meta.get("kind") or DATASET_KIND
                data = meta.get("data") or {}
                source = data.get("FileSource") or ((data.get("DatasetProperties") or {}).get("FileSourceInfo") or {}).get("FileSource")
                if not source:
                    return self._error(400, "File metadata requires a FileSource")
                fname = Path(str(source)).name
                target = FILES_DIR / fname
                if fname not in FILE_META and not target.exists():
                    return self._error(400, f"File not found for FileSource: {fname}")
                rec = deepcopy(meta)
                rec["id"] = rid
                rec["kind"] = kind
                rec.setdefault("acl", deepcopy(DEFAULT_ACL))
                legal = rec.setdefault("legal", {})
                if not legal.get("legaltags"):
                    legal["legaltags"] = [LEGAL_TAG]
                legal["otherRelevantDataCountries"] = legal.get("otherRelevantDataCountries", [])
                try:
                    upsert_records([rec])
                except ValueError as e:
                    return self._error(400, str(e))
                created_ids.append({"id": rid})
            return self._json(201, created_ids[0] if len(created_ids) == 1 else {"ids": created_ids})

        if path in {"/api/file/v2/getLocation", "/file/v2/getLocation"}:
            if not self._rbac({"users.datalake.editors"}):
                return
            file_id = payload.get("FileID", "") if isinstance(payload, dict) else ""
            token = uuid.uuid4().hex
            host = self.headers.get("Host", f"localhost:{PORT}")
            with STORE_LOCK:
                UPLOAD_TOKENS[token] = {"created": time.time(), "kindSubType": "dataset--File.Generic"}
            return self._json(200, {
                "fileID": file_id,
                "Location": {
                    "SignedURL": f"http://{host}/upload/{token}",
                    "expiresInSeconds": int(UPLOAD_TOKEN_TTL),
                },
            })

        if path in {"/api/file/v2/delivery/getFileSignedUrl", "/file/v2/delivery/getFileSignedUrl"}:
            if not self._rbac({"users.datalake.viewers"}):
                return
            srns = payload.get("srn") or payload.get("srns") or []
            if isinstance(srns, str):
                srns = [srns]
            host = self.headers.get("Host", f"localhost:{PORT}")
            processed = {}
            unprocessed = []
            for srn in srns:
                fname = dataset_file_name(str(srn))
                if not fname:
                    unprocessed.append(srn)
                    continue
                body = {"signedUrl": f"http://{host}/files/{fname}", "expiresInSeconds": 3600}
                meta = FILE_META.get(fname)
                if meta:
                    body["size"] = meta["size"]
                    body["sha256"] = meta["sha256"]
                processed[srn] = body
            return self._json(200, {"processed": processed, "unprocessed": unprocessed})

        if path in {"/api/file/v2/files", "/file/v2/files"}:
            if not self._rbac({"service.storage.editor"}):
                return
            records = payload.get("fileRecord", payload if isinstance(payload, list) else None)
            if records is None:
                records = [payload]
            if isinstance(records, dict):
                records = [records]
            created_files = []
            host = self.headers.get("Host", f"localhost:{PORT}")
            for rec in records:
                if not isinstance(rec, dict) or "id" not in rec or "kind" not in rec:
                    return self._error(400, "Each file body requires a dataset record with id and kind")
                try:
                    upsert_records([rec])
                except ValueError as e:
                    return self._error(400, str(e))
                rec_id = rec["id"]
                fname = dataset_file_name(rec_id) or rec.get("data", {}).get("FileName")
                meta = FILE_META.get(fname, {})
                location = {"expiresInSeconds": 3600}
                if fname:
                    location["signedUrl"] = f"http://{host}/files/{fname}"
                if meta:
                    location["size"] = meta["size"]
                    location["sha256"] = meta["sha256"]
                ts = RECORD_TS.get(rec_id, {})
                created_files.append({
                    "fileID": rec_id,
                    "fileInitTime": _dt.datetime.fromtimestamp(
                        ts.get("createTime", time.time()), tz=_dt.timezone.utc
                    ).isoformat(),
                    "fileSourceDatasWithLocation": [
                        {"fileSource": rec.get("data", {}).get("FileName"), "location": location}
                    ],
                })
            result = created_files[0] if len(created_files) == 1 else created_files
            return self._json(200, result)

        if path in {"/api/workflow/v1/workflow", "/workflow/v1/workflow"}:
            if not self._rbac({"service.storage.editor"}):
                return
            name = payload.get("workflowName")
            if not name or not isinstance(name, str):
                return self._error(400, "workflowName is required")
            wf = deepcopy(payload)
            wf.setdefault("workflowId", "wf-" + uuid.uuid4().hex[:12])
            wf.setdefault("createdby", "osdu-lite")
            wf.setdefault("creationTimestamp", time.time())
            with WORKFLOW_LOCK:
                WORKFLOW_DEFS[name] = wf
            return self._json(200, deepcopy(wf))

        for prefix in ("/api/workflow/v1/workflow/", "/workflow/v1/workflow/"):
            if path.startswith(prefix):
                rest = unquote(path[len(prefix):])
                segs = rest.split("/")
                if len(segs) != 2 or segs[1] != "workflowRun":
                    return self._error(404, f"Unknown workflow endpoint: {path}")
                name = segs[0]
                if name not in WORKFLOW_DEFS:
                    return self._error(404, f"Workflow not found: {name}")
                if not self._rbac({"service.storage.editor"}):
                    return
                if name == "Osdu_ingest":
                    records = manifest_records(payload)
                    try:
                        result = upsert_records(records)
                    except ValueError as e:
                        return self._error(400, str(e))
                    run_id = "workflow-" + uuid.uuid4().hex[:12]
                    created = time.time()
                    with WORKFLOW_LOCK:
                        WORKFLOW_RUNS[run_id] = {
                            "workflowName": "Osdu_ingest",
                            "status": "running",
                            "recordCount": len(result),
                            "recordIds": result,
                            "createdAt": created,
                            "updatedAt": created,
                        }
                    def finalize():
                        time.sleep(INDEX_DELAY_SECONDS)
                        with WORKFLOW_LOCK:
                            run = WORKFLOW_RUNS.get(run_id)
                            if run:
                                run["status"] = "succeeded"
                                run["updatedAt"] = time.time()
                    threading.Thread(target=finalize, daemon=True).start()
                    return self._json(200, {
                        "workflowRunId": run_id,
                        "workflowName": "Osdu_ingest",
                        "status": "submitted",
                        "recordCount": len(result),
                        "recordIds": result,
                    })
                run_id = "workflow-" + uuid.uuid4().hex[:12]
                created = time.time()
                with WORKFLOW_LOCK:
                    WORKFLOW_RUNS[run_id] = {
                        "workflowName": name,
                        "status": "running",
                        "createdAt": created,
                        "updatedAt": created,
                    }
                def finalize_generic():
                    time.sleep(INDEX_DELAY_SECONDS)
                    with WORKFLOW_LOCK:
                        run = WORKFLOW_RUNS.get(run_id)
                        if run:
                            run["status"] = "succeeded"
                            run["updatedAt"] = time.time()
                threading.Thread(target=finalize_generic, daemon=True).start()
                return self._json(200, {
                    "workflowRunId": run_id,
                    "workflowName": name,
                    "status": "submitted",
                })

        if path in {"/api/schema-service/v1/schema", "/schema-service/v1/schema"}:
            if not self._rbac({"service.storage.editor"}):
                return
            key = payload.get("kind") or payload.get("schemaInfo", {}).get("schemaIdentity", {}).get("entityType")
            if not key:
                return self._error(400, "Schema requires kind or schemaInfo.schemaIdentity")
            storable = deepcopy(payload)
            storable.setdefault("kind", key)
            with STORE_LOCK:
                SCHEMAS[key] = storable
            return self._json(201, storable)

        if path == "/poc/reset":
            clear_state()
            return self._json(200, {
                "status": "reset",
                "storedRecords": 0,
                "indexedRecords": 0,
                "currentDataProfile": current_data_profile(),
                "recordsByKind": record_counts(),
            })

        if path == "/poc/load-full-sample":
            load_full_sample(index_immediately=True)
            counts = record_counts()
            return self._json(200, {
                "status": "loaded",
                "storedRecords": len(RECORD_BY_ID),
                "indexedRecords": len(SEARCH_BY_ID),
                "currentDataProfile": current_data_profile(),
                "preloadedFullSample": is_full_sample_loaded(),
                **counts,
            })

        self._error(404, f"Unknown endpoint: {path}")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._check_auth(path):
            return

        if path.startswith("/upload/"):
            token = path[len("/upload/"):]
            with STORE_LOCK:
                if token not in UPLOAD_TOKENS:
                    return self._error(404, "Unknown/expired upload token")
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            filename = self.headers.get("x-file-name") or f"uploaded-{token}.bin"
            filename = Path(filename).name
            (FILES_DIR / filename).write_bytes(body)
            with STORE_LOCK:
                UPLOAD_TOKENS[token]["fileName"] = filename
                FILE_META[filename] = {
                    "size": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            return self._json(200, {
                "status": "uploaded",
                "fileName": filename,
                "size": len(body),
                "sha256": FILE_META[filename]["sha256"],
            })

        sweep_temp_state()
        payload = self._read_json()
        if payload is None:
            return

        if path in {"/api/legal/v1/legaltags", "/legal/v1/legaltags"}:
            if not self._rbac({"users.datalake.editors"}):
                return
            name = payload.get("name")
            if not name or name not in LEGAL_TAGS:
                return self._error(404, f"LegalTag not found: {name}")
            props = LEGAL_TAGS[name].get("properties", {})
            incoming = payload.get("properties", {})
            if incoming and isinstance(incoming, dict):
                for key in ("contractId", "expirationDate", "extensionProperties"):
                    if key in incoming:
                        props[key] = incoming[key]
            if payload.get("description") is not None:
                LEGAL_TAGS[name]["description"] = payload["description"]
            return self._json(200, {**LEGAL_TAGS[name], "isValid": is_legal_tag_valid(name)})

        if path in {"/api/storage/v2/records", "/storage/v2/records"}:
            if not self._rbac({"service.storage.editor"}):
                return
            records = payload if isinstance(payload, list) else [payload]
            try:
                result = upsert_records(records)
            except ValueError as e:
                return self._error(400, str(e))
            return self._json(200, {
                "recordCount": len(result),
                "recordIds": result,
                "skippedRecordIds": [],
            })

        if path in {"/api/dataset/v1/registerDataset", "/dataset/v1/registerDataset"}:
            if not self._rbac({"service.storage.editor"}):
                return
            is_batch = isinstance(payload, dict) and "datasetRegistries" in payload
            records = payload.get("datasetRegistries") if is_batch else payload.get("datasets")
            if records is None:
                records = payload if isinstance(payload, list) else [payload]
            try:
                result = upsert_records(records)
            except ValueError as e:
                return self._error(400, str(e))
            if is_batch:
                ids = [entry["id"] for entry in result]
                with STORE_LOCK:
                    stored = [deepcopy(RECORD_BY_ID[eid]) for eid in ids if eid in RECORD_BY_ID]
                return self._json(200, {"datasetRegistries": stored})
            return self._json(200, {
                "status": "registered",
                "recordCount": len(result),
                "recordIds": result,
            })

        if path in {"/api/schema-service/v1/schema", "/schema-service/v1/schema"}:
            if not self._rbac({"service.storage.editor"}):
                return
            key = payload.get("kind") or payload.get("schemaInfo", {}).get("schemaIdentity", {}).get("entityType")
            if not key:
                return self._error(400, "Schema requires kind or schemaInfo.schemaIdentity")
            storable = deepcopy(payload)
            storable.setdefault("kind", key)
            with STORE_LOCK:
                SCHEMAS[key] = storable
            return self._json(200, storable)

        if path in {"/api/schema-service/v1/schemas/system", "/schema-service/v1/schemas/system",
                    "/api/schema-service/v1/schemas/system/", "/schema-service/v1/schemas/system/"}:
            if not self._rbac({"service.storage.editor"}):
                return
            storable = deepcopy(payload)
            with STORE_LOCK:
                SCHEMAS["system"] = storable
            return self._json(200, storable)

        for prefix in ("/api/workflow/v1/workflow/", "/workflow/v1/workflow/"):
            if path.startswith(prefix):
                rest = unquote(path[len(prefix):])
                segs = rest.split("/")
                if len(segs) != 3 or segs[1] != "workflowRun":
                    return self._error(404, f"Unknown workflow endpoint: {path}")
                name, run_id = segs[0], segs[2]
                if not self._rbac({"service.storage.editor"}):
                    return
                with WORKFLOW_LOCK:
                    run = WORKFLOW_RUNS.get(run_id)
                    if not run:
                        return self._error(404, f"Workflow run not found: {run_id}")
                    if payload.get("status"):
                        run["status"] = payload["status"]
                    run["updatedAt"] = time.time()
                return self._json(200, {"workflowRunId": run_id, **run})

        self._error(404, f"Unknown endpoint: {path}")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not self._check_auth(path):
            return
        sweep_temp_state()

        if path in {"/api/storage/v2/records", "/storage/v2/records"}:
            if not self._rbac({"service.storage.editor"}):
                return
            payload = self._read_json()
            if payload is None:
                return
            ids = payload.get("records", []) if isinstance(payload, dict) else payload
            if not isinstance(ids, list):
                return self._error(400, "Expected a list of record ids ({\"records\": [...]})")
            removed = soft_delete_records(ids)
            return self._json(200, {
                "recordCount": len(removed),
                "recordIds": [{"id": rid} for rid in removed],
                "skippedRecordIds": [{"id": rid} for rid in ids if rid not in removed],
            })

        for prefix in ("/api/search/v2/query_with_cursor/", "/search/v2/query_with_cursor/"):
            if path.startswith(prefix):
                cursor = unquote(path[len(prefix):])
                with CURSOR_LOCK:
                    CURSORS.pop(cursor, None)
                return self._no_content()

        for prefix in ("/api/storage/v2/records/versions/", "/storage/v2/records/versions/"):
            if path.startswith(prefix):
                if not self._rbac({"service.storage.editor"}):
                    return
                rid = unquote(path[len(prefix):])
                with STORE_LOCK:
                    if rid not in RECORD_BY_ID and not RECORD_VERSIONS.get(rid):
                        return self._error(404, f"Record not found: {rid}")
                    RECORD_BY_ID.pop(rid, None)
                    SEARCH_BY_ID.pop(rid, None)
                    RECORD_VERSIONS.pop(rid, None)
                    RECORD_TS.pop(rid, None)
                    DELETED.discard(rid)
                return self._no_content()

        for prefix in ("/api/storage/v2/records/", "/storage/v2/records/"):
            if path.startswith(prefix):
                if not self._rbac({"service.storage.editor"}):
                    return
                rid = unquote(path[len(prefix):])
                hard = query.get("hardDelete", ["false"])[0].lower() in {"1", "true", "yes"}
                with STORE_LOCK:
                    if rid not in RECORD_BY_ID:
                        return self._error(404, f"Record not found: {rid}")
                    if hard:
                        RECORD_BY_ID.pop(rid, None)
                        SEARCH_BY_ID.pop(rid, None)
                        RECORD_VERSIONS.pop(rid, None)
                        DELETED.discard(rid)
                        RECORD_TS.pop(rid, None)
                    else:
                        DELETED.add(rid)
                        SEARCH_BY_ID.pop(rid, None)
                return self._no_content()

        for prefix in ("/api/legal/v1/legaltags/", "/legal/v1/legaltags/"):
            if path.startswith(prefix):
                if not self._rbac({"users.datalake.editors"}):
                    return
                name = unquote(path[len(prefix):])
                if name not in LEGAL_TAGS:
                    return self._error(404, f"LegalTag not found: {name}")
                del LEGAL_TAGS[name]
                return self._no_content()

        for prefix in ("/api/entitlements/v2/groups/", "/entitlements/v2/groups/"):
            if not path.startswith(prefix):
                continue
            rest = unquote(path[len(prefix):])
            if "/members/" in rest:
                group_email_value, member_email = rest.split("/members/", 1)
                if not self._rbac({"service.entitlements.admin", "service.entitlements.user"}):
                    return
                with GROUPS_LOCK:
                    group = GROUPS.get(group_email_value)
                    if not group:
                        return self._error(404, f"Group not found: {group_email_value}")
                    if member_email not in group["members"]:
                        return self._error(404, f"Member not found: {member_email}")
                    del group["members"][member_email]
                return self._no_content()
            if not self._rbac({"service.entitlements.admin"}):
                return
            with GROUPS_LOCK:
                if rest not in GROUPS:
                    return self._error(404, f"Group not found: {rest}")
                del GROUPS[rest]
            return self._no_content()

        if path.startswith("/api/file/v2/files/") or path.startswith("/file/v2/files/"):
            prefix = "/api/file/v2/files/" if path.startswith("/api/file/v2/files/") else "/file/v2/files/"
            rest = unquote(path[len(prefix):])
            if rest.endswith("/metadata"):
                if not self._rbac({"users.datalake.editors"}):
                    return
                fid = rest[:-len("/metadata")]
                with STORE_LOCK:
                    if fid not in RECORD_BY_ID:
                        return self._error(404, f"File not found: {fid}")
                soft_delete_records([fid])
                return self._no_content()

        for prefix in ("/api/workflow/v1/workflow/", "/workflow/v1/workflow/"):
            if path.startswith(prefix):
                rest = unquote(path[len(prefix):])
                segs = rest.split("/")
                if len(segs) == 3 and segs[1] == "workflowRun":
                    name, run_id = segs[0], segs[2]
                    if not self._rbac({"service.storage.editor"}):
                        return
                    with WORKFLOW_LOCK:
                        if run_id not in WORKFLOW_RUNS:
                            return self._error(404, f"Workflow run not found: {run_id}")
                        del WORKFLOW_RUNS[run_id]
                    return self._no_content()
                if len(segs) == 1:
                    if not self._rbac({"service.storage.editor"}):
                        return
                    name = segs[0]
                    with WORKFLOW_LOCK:
                        if name not in WORKFLOW_DEFS:
                            return self._error(404, f"Workflow not found: {name}")
                        del WORKFLOW_DEFS[name]
                        for run_id in [rid for rid, r in WORKFLOW_RUNS.items() if r.get("workflowName") == name]:
                            del WORKFLOW_RUNS[run_id]
                    return self._no_content()
                return self._error(404, f"Unknown workflow endpoint: {path}")

        self._error(404, f"Unknown endpoint: {path}")

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._check_auth(path):
            return
        payload = self._read_json()
        if payload is None:
            return

        for prefix in ("/api/entitlements/v2/groups/", "/entitlements/v2/groups/"):
            if path.startswith(prefix):
                email = unquote(path[len(prefix):])
                if not self._rbac({"service.entitlements.user", "service.entitlements.admin"}):
                    return
                with GROUPS_LOCK:
                    group = GROUPS.get(email)
                    if not group:
                        return self._error(404, f"Group not found: {email}")
                    if isinstance(payload.get("op"), str):
                        op = payload["op"]
                        target = payload.get("path")
                        if op in {"replace", "add"} and target == "/appIds":
                            group["appIds"] = list(payload.get("value") or [])
                        elif op in {"replace", "add"} and target == "/description":
                            group["description"] = payload.get("value")
                        elif op in {"replace", "add"} and target == "/name":
                            group["name"] = payload.get("value")
                        else:
                            return self._error(400, f"Unsupported JSON Patch op/path: {op} {target}")
                        return self._json(200, deepcopy(group))
                    for key in ("name", "description"):
                        if payload.get(key) is not None:
                            group[key] = payload[key]
                    if payload.get("appIds") is not None:
                        group["appIds"] = list(payload["appIds"])
                return self._no_content()

        if path in {"/api/storage/v2/records", "/storage/v2/records"}:
            if not self._rbac({"service.storage.editor"}):
                return
            records = payload if isinstance(payload, list) else [payload]
            result = []
            for rec in records:
                if not isinstance(rec, dict) or "id" not in rec:
                    return self._error(400, "Each patch part requires an id")
                updated = patch_record(rec["id"], rec)
                if not updated:
                    return self._error(404, f"Record not found: {rec['id']}")
            return self._json(200, {
                "recordCount": len(records),
                "recordIds": [r["id"] for r in records],
                "skippedRecordIds": [],
            })

        for prefix in ("/api/storage/v2/records/", "/storage/v2/records/"):
            if path.startswith(prefix) and not path.startswith("/api/storage/v2/records/versions/"):
                if not self._rbac({"service.storage.editor"}):
                    return
                rid = unquote(path[len(prefix):])
                updated = patch_record(rid, payload)
                if not updated:
                    return self._error(404, f"Record not found: {rid}")
                return self._json(200, {
                    "recordCount": 1,
                    "recordIds": [updated[0]["id"]],
                    "skippedRecordIds": [],
                })

        self._error(404, f"Unknown endpoint: {path}")

def main():
    print(f"OSDU Well 360 local server v0.7 listening on http://0.0.0.0:{PORT}")
    print(f"Partition: {PARTITION} | token: {TOKEN if REQUIRE_AUTH else '(auth disabled)'}")
    print(f"Preload full sample configured: {PRELOAD_FULL_SAMPLE} | index delay: {INDEX_DELAY_SECONDS}s")
    print(f"Upload token TTL: {UPLOAD_TOKEN_TTL}s | cursor TTL: {CURSOR_TTL}s")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

if __name__ == "__main__":
    main()