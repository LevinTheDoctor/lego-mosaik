"""Garage einrichten: Layout zuweisen, S3-Key importieren, Bucket anlegen (idempotent).

Nutzt die Admin-API v2 von Garage (``/v2/...``, Bearer-Token). Läuft im Helm-Job
``garage-init`` und in ``compose.yaml``. Der S3-Key wird mit vorgegebener ID und
vorgegebenem Secret importiert: So kennt die App ihn aus demselben Kubernetes-Secret,
ohne dass der Job etwas zurückschreiben muss.

Format der Keys (prüft Garage): ID ``GK`` + 24 Hex-Zeichen, Secret 64 Hex-Zeichen.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0
ALL_PERMISSIONS = {"read": True, "write": True, "owner": True}


@dataclass(frozen=True, slots=True)
class GarageSetup:
    """Was eingerichtet werden soll.

    Attributes:
        nodes: So viele Knoten müssen verbunden sein, bevor das Layout entsteht.
        zone: Zone für alle Knoten ohne Rolle.
        capacity_bytes: Speicher je Knoten.
        bucket: Name (globaler Alias) des Buckets.
        access_key_id: ID des S3-Keys (``GK…``).
        secret_access_key: Secret des S3-Keys.
        key_name: Anzeigename des Keys in Garage.
    """

    nodes: int
    zone: str
    capacity_bytes: int
    bucket: str
    access_key_id: str
    secret_access_key: str
    key_name: str = "lego-mosaik"


class GarageAdmin:
    """Kleiner Client für die benötigten Aufrufe der Admin-API v2."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def call(self, method: str, endpoint: str, **kwargs: Any) -> httpx.Response:
        return self._client.request(method, f"/v2/{endpoint}", **kwargs)

    def json(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        response = self.call(method, endpoint, **kwargs)
        response.raise_for_status()
        return response.json()


def wait_for_nodes(admin: GarageAdmin, nodes: int, timeout_seconds: float) -> None:
    """Wartet, bis die Admin-API antwortet und ``nodes`` Knoten verbunden sind."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            health = admin.json("GET", "GetClusterHealth")
            if int(health.get("connectedNodes", 0)) >= nodes:
                return
            logger.info("Garage: %s von %d Knoten verbunden.", health.get("connectedNodes"), nodes)
        except httpx.HTTPError as error:
            logger.info("Garage noch nicht erreichbar: %s", error)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Garage hat nach {timeout_seconds:.0f} s nicht {nodes} Knoten.")
        time.sleep(POLL_SECONDS)


def assign_layout(admin: GarageAdmin, zone: str, capacity_bytes: int) -> bool:
    """Gibt jedem Knoten ohne Rolle eine Rolle und wendet das Layout an.

    Returns:
        True, wenn sich das Layout geändert hat.
    """
    status = admin.json("GET", "GetClusterStatus")
    layout = admin.json("GET", "GetClusterLayout")
    with_role = {role["id"] for role in layout.get("roles", [])}
    with_role |= {change["id"] for change in layout.get("stagedRoleChanges", [])}
    missing = [node["id"] for node in status["nodes"] if node["id"] not in with_role]
    if not missing and not layout.get("stagedRoleChanges"):
        logger.info("Garage-Layout ist vollständig (Version %d).", layout["version"])
        return False
    if missing:
        roles = [
            {"id": node_id, "zone": zone, "capacity": capacity_bytes, "tags": []}
            for node_id in missing
        ]
        admin.json("POST", "UpdateClusterLayout", json={"roles": roles})
    version = int(layout["version"]) + 1
    admin.json("POST", "ApplyClusterLayout", json={"version": version})
    logger.info("Garage-Layout Version %d angewendet (%d neue Knoten).", version, len(missing))
    return True


def ensure_key(admin: GarageAdmin, access_key_id: str, secret: str, name: str) -> None:
    """Importiert den S3-Key, falls Garage ihn noch nicht kennt."""
    if admin.call("GET", "GetKeyInfo", params={"id": access_key_id}).is_success:
        logger.info("S3-Key %s ist schon vorhanden.", access_key_id)
        return
    admin.json(
        "POST",
        "ImportKey",
        json={"accessKeyId": access_key_id, "secretAccessKey": secret, "name": name},
    )
    logger.info("S3-Key %s importiert.", access_key_id)


def ensure_bucket(admin: GarageAdmin, bucket: str, access_key_id: str) -> str:
    """Legt den Bucket an (falls nötig) und gibt dem Key volle Rechte darauf.

    Returns:
        Die interne Bucket-ID.
    """
    response = admin.call("GET", "GetBucketInfo", params={"globalAlias": bucket})
    if response.is_success:
        bucket_id = str(response.json()["id"])
    else:
        bucket_id = str(admin.json("POST", "CreateBucket", json={"globalAlias": bucket})["id"])
        logger.info("Bucket %s angelegt.", bucket)
    admin.json(
        "POST",
        "AllowBucketKey",
        json={"bucketId": bucket_id, "accessKeyId": access_key_id, "permissions": ALL_PERMISSIONS},
    )
    return bucket_id


def check_key_format(access_key_id: str, secret: str) -> list[str]:
    """Fehler im Format der Keys (leer = in Ordnung)."""
    problems: list[str] = []
    if not (access_key_id.startswith("GK") and _is_hex(access_key_id[2:], 24)):
        problems.append("Die Key-ID muss „GK“ plus 24 Hex-Zeichen sein.")
    if not _is_hex(secret, 64):
        problems.append("Das Secret muss aus 64 Hex-Zeichen bestehen.")
    return problems


def _is_hex(text: str, length: int) -> bool:
    return len(text) == length and all(char in "0123456789abcdef" for char in text.lower())


def run_setup(admin: GarageAdmin, setup: GarageSetup, timeout_seconds: float) -> None:
    """Komplette Einrichtung; mehrfach ausführbar, ändert nur, was fehlt.

    Raises:
        ValueError: Bei falschem Key-Format.
        TimeoutError: Wenn Garage nicht rechtzeitig bereit ist.
    """
    problems: Sequence[str] = check_key_format(setup.access_key_id, setup.secret_access_key)
    if problems:
        raise ValueError(" ".join(problems))
    wait_for_nodes(admin, setup.nodes, timeout_seconds)
    assign_layout(admin, setup.zone, setup.capacity_bytes)
    ensure_key(admin, setup.access_key_id, setup.secret_access_key, setup.key_name)
    ensure_bucket(admin, setup.bucket, setup.access_key_id)
    logger.info("Garage ist eingerichtet: Bucket %s.", setup.bucket)
