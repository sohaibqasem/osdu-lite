import os
import time
import uuid
import requests

class OsduClient:
    def __init__(self, base_url, partition, token):
        self.base_url = base_url.rstrip("/")
        self.partition = partition
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "data-partition-id": partition,
            "Content-Type": "application/json",
        })

    def _headers(self):
        return {"correlation-id": str(uuid.uuid4())}

    def groups(self):
        r = self.session.get(
            f"{self.base_url}/api/entitlements/v1/groups",
            params={"roleRequired": "true"},
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def put_records(self, records):
        r = self.session.put(
            f"{self.base_url}/api/storage/v2/records",
            json=records,
            headers=self._headers(),
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def get_record(self, record_id):
        r = self.session.get(
            f"{self.base_url}/api/storage/v2/records/{record_id}",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def search(self, kind, query=None, returned_fields=None, limit=100):
        payload = {"kind": kind, "limit": limit}
        if query:
            payload["query"] = query
        if returned_fields:
            payload["returnedFields"] = returned_fields

        r = self.session.post(
            f"{self.base_url}/api/search/v2/query",
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def run_manifest(self, manifest_payload):
        r = self.session.post(
            f"{self.base_url}/api/workflow/v1/workflow/Osdu_ingest/workflowRun",
            json=manifest_payload,
            headers=self._headers(),
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def wait_until_searchable(self, kind, record_id, timeout_seconds=120):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            result = self.search(kind, f'id:"{record_id}"', ["id"], 1)
            if result.get("results"):
                return True
            time.sleep(3)
        return False


if __name__ == "__main__":
    client = OsduClient(
        os.environ["OSDU_BASE_URL"],
        os.environ["OSDU_PARTITION"],
        os.environ["OSDU_TOKEN"],
    )

    results = client.search(
        "osdu:wks:master-data--Well:*",
        'data.FacilityName:"FALCON-01"',
        ["id", "data.FacilityName"],
        10,
    )
    print(results)
