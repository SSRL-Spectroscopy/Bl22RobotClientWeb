"""A stand-in for the mail-in XAS portal, for tests (an httpx mock transport).

Answers in the shapes documented in reference/instrument-api.md.
"""

import json
import secrets

import httpx
from pydantic import SecretStr

from robotweb.portal import PortalClient


def measurement(mid: str, number: int = 1, **changes) -> dict:
    base = {
        "id": mid,
        "number": number,
        "technique": "exafs",
        "element": "Fe",
        "edge": "K",
        "detection": "transmission_sdd",
        "beam": "unfocused",
        "status": "pending",
        "error_message": None,
        "last_batch": None,
        "last_sweep": None,
    }
    return {**base, **changes}


def frame(barcode: str = "KQZ-TBW", container: int = 2, slot: int = 7, **changes) -> dict:
    base = {
        "barcode": barcode,
        "state": "internal_storage",
        "locked": True,
        "safe": True,
        "storage": {"container": container, "slot": slot, "since": "2026-10-18T09:12:44Z"},
        "owner": "Jane Doe",
        "plates": {"left": "8-circle", "right": "8-slot"},
        "positions": [
            {
                "position": "Left-A",
                "plate_type": "8-circle",
                "sample_id": "a" * 24,
                "sample_name": "Fe2O3 reference",
                "measurements": [
                    measurement("1" * 24, 1),
                    measurement("2" * 24, 2, technique="xanes", status="done", last_batch=1, last_sweep=8),
                ],
            },
            {
                "position": "Right-C",
                "plate_type": "8-slot",
                "sample_id": "b" * 24,
                "sample_name": "Ni foil",
                "measurements": [measurement("3" * 24, 1, element="Ni", detection="transmission")],
            },
        ],
    }
    return {**base, **changes}


class FakePortal:
    def __init__(self) -> None:
        self.key = "htxas_" + secrets.token_urlsafe(30)
        self.frames: dict[str, dict] = {"KQZ-TBW": frame()}
        self.requests: list[tuple[str, str, dict | None]] = []
        self.down = False

    def client(self, *, key: str | None = None) -> PortalClient:
        return PortalClient(
            "http://portal.test/mail-in-xas/api",
            SecretStr(key if key is not None else self.key),
            transport=httpx.MockTransport(self._handle),
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path.removeprefix("/mail-in-xas/api")
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))

        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database": "ok", "environment": "dev", "version": "2.7.1"})
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"detail": "API key required"})

        if path == "/instrument/queue":
            waiting = [
                f
                for f in self.frames.values()
                if f["state"] == "internal_storage"
                and f["locked"]
                and any(m["status"] == "pending" for p in f["positions"] for m in p["measurements"])
            ]
            return httpx.Response(200, json=waiting)
        if path == "/instrument/storage/internal":
            return httpx.Response(
                200,
                json=[
                    {
                        "barcode": f["barcode"],
                        "container": f["storage"]["container"],
                        "slot": f["storage"]["slot"],
                        "state": f["state"],
                        "locked": f["locked"],
                    }
                    for f in self.frames.values()
                    if f["storage"]
                ],
            )
        if path.startswith("/instrument/frames/"):
            parts = path.split("/")
            found = self.frames.get(parts[3].upper())
            if found is None:
                return httpx.Response(404, json={"detail": "Unknown barcode"})
            if len(parts) == 4 and request.method == "GET":
                return httpx.Response(200, json=found)
            if len(parts) == 5 and parts[4] == "state" and request.method == "POST":
                allowed = {
                    ("internal_storage", "scheduled"),
                    ("scheduled", "loaded"),
                    ("scheduled", "internal_storage"),
                    ("loaded", "internal_storage"),
                }
                if (found["state"], body["state"]) not in allowed:
                    return httpx.Response(
                        409, json={"detail": f"Cannot move from {found['state']} to {body['state']}"}
                    )
                found["state"] = body["state"]
                return httpx.Response(200, json=found)
        if path.startswith("/instrument/measurements/") and path.endswith("/status"):
            mid = path.split("/")[3]
            for f in self.frames.values():
                for p in f["positions"]:
                    for m in p["measurements"]:
                        if m["id"] == mid:
                            m["status"] = body["status"]
                            m["error_message"] = body.get("error_message")
                            return httpx.Response(200, json=m)
            return httpx.Response(404, json={"detail": "Unknown measurement"})
        return httpx.Response(422, json={"detail": [{"loc": ["path"], "msg": "not handled", "type": "test"}]})
