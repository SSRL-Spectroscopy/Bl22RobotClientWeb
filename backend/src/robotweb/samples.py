"""Frames and measurements as the rest of the backend sees them.

Turns the portal's frame JSON (reference/instrument-api.md, section 4.2) into
a flat structure: one frame with a list of measurements. Values this software
does not know (a new plate type, a new technique) do not cause a failure; the
measurement is marked as not measurable, with the reason.
"""

from typing import Any

TECHNIQUES = {"xanes", "exafs"}
EDGES = {"K", "L1", "L2", "L3"}
DETECTIONS = {"transmission", "transmission_tfy", "transmission_sdd"}
BEAMS = {"unfocused", "focused"}
PLATE_TYPES = {"8-circle", "8-slot", "8-circle-gas"}


def normalise_frame(raw: dict) -> dict:
    storage = raw.get("storage") or {}
    measurements: list[dict] = []
    for position in raw.get("positions") or []:
        for measurement in position.get("measurements") or []:
            measurements.append(_normalise_measurement(position, measurement))
    return {
        "barcode": str(raw.get("barcode", "")).upper(),
        "state": raw.get("state"),
        "locked": bool(raw.get("locked")),
        "safe": bool(raw.get("safe")),
        "container": storage.get("container"),
        "slot": storage.get("slot"),
        "owner": raw.get("owner"),
        "plates": raw.get("plates") or {},
        "measurements": measurements,
    }


def _normalise_measurement(position: dict, raw: dict) -> dict:
    measurement = {
        "id": raw.get("id"),
        "position": position.get("position"),
        "plate_type": position.get("plate_type"),
        "sample_id": position.get("sample_id"),
        "sample_name": position.get("sample_name"),
        "number": raw.get("number"),
        "technique": raw.get("technique"),
        "element": raw.get("element"),
        "edge": raw.get("edge"),
        "detection": raw.get("detection"),
        "beam": raw.get("beam"),
        "status": raw.get("status"),
        "error_message": raw.get("error_message"),
        "last_batch": raw.get("last_batch"),
        "last_sweep": raw.get("last_sweep"),
    }
    problem = _why_not_measurable(measurement)
    measurement["measurable"] = problem is None
    measurement["problem"] = problem
    return measurement


def _why_not_measurable(m: dict[str, Any]) -> str | None:
    if not m["id"]:
        return "the portal gave no measurement id"
    checks = (
        ("plate_type", PLATE_TYPES, "plate type"),
        ("technique", TECHNIQUES, "technique"),
        ("edge", EDGES, "edge"),
        ("detection", DETECTIONS, "detection mode"),
        ("beam", BEAMS, "beam mode"),
    )
    for field, known, word in checks:
        if m[field] not in known:
            return f"unknown {word} {m[field]!r}"
    if not m["element"] or not m["position"]:
        return "element or position missing"
    return None


def describe(m: dict) -> str:
    """One line for logs and messages: "Left-A Fe K exafs"."""
    return f"{m['position']} {m['element']} {m['edge']} {m['technique']}"
