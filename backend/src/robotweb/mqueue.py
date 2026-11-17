"""The measurement queue: which frames, and which of their measurements, the
next sequence run will work through, in order.

Built by the operator at the helm from the portal's list. Saved to a file so
that a backend restart does not lose the selection. The published copy (which
every browser receives, Observers included) leaves out owner and sample names.
"""

import json
import os
import time
from pathlib import Path

from .audit import AuditLog
from .state import StateStore

QUEUE_KEY = "queue"

_PUBLIC_MEASUREMENT_FIELDS = (
    "id", "position", "plate_type", "number", "technique", "element", "edge", "detection", "beam", "state",
    "status",
)

# Portal statuses that may be queued. "done" and "error" mean the measurement
# is run again; the sequence must set it back to "pending" in the portal first,
# so that the control server's upload starts a new batch (instrument-api.md 4.5).
QUEUEABLE_STATUSES = ("pending", "done", "error")


class QueueError(Exception):
    """The requested change to the queue is not possible."""


class MeasurementQueue:
    def __init__(self, path: Path, store: StateStore, audit: AuditLog) -> None:
        self._path = path
        self._store = store
        self._audit = audit
        self._items: list[dict] = []
        self._load()
        self._publish()

    @property
    def items(self) -> list[dict]:
        return self._items

    def set_frame(self, frame: dict, measurement_ids: list[str], *, user: str) -> None:
        """Add a frame with the chosen measurements, or change the choice for a
        frame that is already queued (it keeps its place in the order).

        `frame` is a freshly read, normalised frame (see samples.py).
        """
        barcode = frame["barcode"]
        if frame["state"] != "internal_storage":
            raise QueueError(f"Frame {barcode} is not in internal storage (portal state: {frame['state']}).")
        if not frame["locked"]:
            raise QueueError(f"Frame {barcode} is not locked in the portal.")
        if not frame["safe"]:
            raise QueueError(f"Frame {barcode} is not declared safe in the portal.")
        if frame["container"] is None or frame["slot"] is None:
            raise QueueError(f"The portal lists no storage slot for frame {barcode}.")
        if not measurement_ids:
            raise QueueError("Select at least one measurement.")
        if len(set(measurement_ids)) != len(measurement_ids):
            raise QueueError("A measurement was selected twice.")

        by_id = {m["id"]: m for m in frame["measurements"]}
        chosen = []
        for measurement_id in measurement_ids:
            measurement = by_id.get(measurement_id)
            if measurement is None:
                raise QueueError(f"Frame {barcode} has no measurement {measurement_id}.")
            if not measurement["measurable"]:
                raise QueueError(f"A selected measurement cannot be measured: {measurement['problem']}.")
            if measurement["status"] not in QUEUEABLE_STATUSES:
                raise QueueError(
                    f"A selected measurement has the unknown status '{measurement['status']}' in the portal."
                )
            chosen.append({**measurement, "state": "queued"})
        # keep the portal's order: by position, then measurement number
        order = {m["id"]: index for index, m in enumerate(frame["measurements"])}
        chosen.sort(key=lambda m: order[m["id"]])

        item = {
            "barcode": barcode,
            "container": frame["container"],
            "slot": frame["slot"],
            "plates": frame["plates"],
            "state": "queued",
            "measurements": chosen,
        }
        for index, existing in enumerate(self._items):
            if existing["barcode"] == barcode:
                self._items[index] = item
                break
        else:
            self._items.append(item)
        self._audit.write("queue_frame_set", user=user, barcode=barcode, measurements=[m["id"] for m in chosen])
        self._save()

    def remove(self, barcode: str, *, user: str) -> None:
        barcode = barcode.upper()
        if not any(item["barcode"] == barcode for item in self._items):
            raise QueueError(f"Frame {barcode} is not in the queue.")
        self._items = [item for item in self._items if item["barcode"] != barcode]
        self._audit.write("queue_frame_removed", user=user, barcode=barcode)
        self._save()

    def reorder(self, barcodes: list[str], *, user: str) -> None:
        barcodes = [b.upper() for b in barcodes]
        current = [item["barcode"] for item in self._items]
        if sorted(barcodes) != sorted(current):
            raise QueueError("The new order must list exactly the frames that are in the queue.")
        by_barcode = {item["barcode"]: item for item in self._items}
        self._items = [by_barcode[b] for b in barcodes]
        self._audit.write("queue_reordered", user=user, order=barcodes)
        self._save()

    def mark(self, barcode: str, state: str, note: str | None = None) -> None:
        """Record what a sequence did with a queued frame."""
        for item in self._items:
            if item["barcode"] == barcode:
                item["state"] = state
                item["note"] = note
                self._save()
                return

    def clear(self, *, user: str) -> None:
        self._items = []
        self._audit.write("queue_cleared", user=user)
        self._save()

    # ---- persistence ------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            items = data["items"]
            if isinstance(items, list):
                self._items = items
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self._audit.write("queue_file_unreadable")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"items": self._items}, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._path)
        self._publish()

    def _publish(self) -> None:
        public = [
            {
                "barcode": item["barcode"],
                "container": item["container"],
                "slot": item["slot"],
                "state": item["state"],
                "note": item.get("note"),
                "measurements": [
                    {field: m.get(field) for field in _PUBLIC_MEASUREMENT_FIELDS} for m in item["measurements"]
                ],
            }
            for item in self._items
        ]
        self._store.publish(QUEUE_KEY, {"items": public, "ts": time.time()})
