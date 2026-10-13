"""Tests für ``garage_init`` gegen eine simulierte Garage-Admin-API (ohne Docker)."""

import json
from typing import Any

import httpx
import pytest

from lego_mosaik_cloud.garage_init import GarageAdmin, GarageSetup, check_key_format, run_setup

KEY_ID = "GK" + "a1" * 12
SECRET = "f0" * 32


class FakeGarage:
    """Merkt sich Layout, Keys und Buckets wie ein Garage-Knoten."""

    def __init__(self, nodes: int = 1) -> None:
        self.node_ids = [f"node{i}" for i in range(nodes)]
        self.roles: list[dict[str, Any]] = []
        self.version = 0
        self.keys: dict[str, str] = {}
        self.buckets: dict[str, str] = {}
        self.grants: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        endpoint = request.url.path.removeprefix("/v2/")
        self.calls.append(endpoint)
        body = json.loads(request.content) if request.content else {}
        match endpoint:
            case "GetClusterHealth":
                return httpx.Response(200, json={"connectedNodes": len(self.node_ids)})
            case "GetClusterStatus":
                return httpx.Response(200, json={"nodes": [{"id": n} for n in self.node_ids]})
            case "GetClusterLayout":
                return httpx.Response(
                    200,
                    json={"version": self.version, "roles": self.roles, "stagedRoleChanges": []},
                )
            case "UpdateClusterLayout":
                self.roles += body["roles"]
                return httpx.Response(200, json={})
            case "ApplyClusterLayout":
                assert body["version"] == self.version + 1
                self.version = body["version"]
                return httpx.Response(200, json={})
            case "GetKeyInfo":
                found = request.url.params["id"] in self.keys
                return httpx.Response(200 if found else 404, json={})
            case "ImportKey":
                self.keys[body["accessKeyId"]] = body["secretAccessKey"]
                return httpx.Response(200, json={})
            case "GetBucketInfo":
                bucket_id = self.buckets.get(request.url.params["globalAlias"])
                return httpx.Response(200 if bucket_id else 404, json={"id": bucket_id})
            case "CreateBucket":
                self.buckets[body["globalAlias"]] = "b-1"
                return httpx.Response(200, json={"id": "b-1"})
            case "AllowBucketKey":
                self.grants.append(body)
                return httpx.Response(200, json={})
        return httpx.Response(404)


def _run(garage: FakeGarage, nodes: int = 1) -> None:
    transport = httpx.MockTransport(garage.handle)
    headers = {"Authorization": "Bearer token"}
    with httpx.Client(transport=transport, base_url="http://garage", headers=headers) as client:
        setup = GarageSetup(nodes, "dc1", 10**9, "lego-mosaik", KEY_ID, SECRET)
        run_setup(GarageAdmin(client), setup, timeout_seconds=1)


def test_sets_up_layout_key_and_bucket() -> None:
    garage = FakeGarage(nodes=3)
    _run(garage, nodes=3)
    assert garage.version == 1 and len(garage.roles) == 3
    assert garage.roles[0] == {"id": "node0", "zone": "dc1", "capacity": 10**9, "tags": []}
    assert garage.keys == {KEY_ID: SECRET}
    assert garage.buckets == {"lego-mosaik": "b-1"}
    assert garage.grants[0]["permissions"] == {"read": True, "write": True, "owner": True}


def test_second_run_changes_nothing() -> None:
    garage = FakeGarage()
    _run(garage)
    garage.calls.clear()
    _run(garage)
    assert garage.version == 1
    assert not {"UpdateClusterLayout", "ApplyClusterLayout", "ImportKey", "CreateBucket"} & set(
        garage.calls
    )


@pytest.mark.parametrize(
    ("key", "secret"), [("AK" + "a1" * 12, SECRET), (KEY_ID, "xyz"), ("GK123", SECRET)]
)
def test_rejects_wrong_key_format(key: str, secret: str) -> None:
    assert check_key_format(key, secret)
