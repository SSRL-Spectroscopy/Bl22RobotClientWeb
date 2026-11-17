"""What is currently loaded on the beamline, as far as the backend knows.

The control server does not record this, so the backend does, to refuse
commands that could cause a collision. This guards against software and
operator mistakes; it is not a safety system.

Each item is in one of three conditions:
  known     the value is trusted (None means "nothing loaded")
  changing  a command that changes it is running
  unknown   the backend cannot vouch for it; an operator must declare it

The record is written to a file on every change. An item found "changing" when
the file is loaded was interrupted mid-command and becomes unknown.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .state import StateStore

EQUIPMENT_KEY = "equipment"

# The frame the robot is holding in the beam: None, or {"container": int,
# "slot": int} for the storage slot it came from. One robot moves frames,
# references and filters, so while this is not None nothing else can be moved.
FRAME = "frame"
REFERENCE = "reference"  # None, or the name of the loaded reference foil
FILTER = "filter"  # None, or the name of the loaded FY filter
ITEMS = (FRAME, REFERENCE, FILTER)

KNOWN, CHANGING, UNKNOWN = "known", "changing", "unknown"


class EquipmentState:
    def __init__(self, path: Path, store: StateStore, audit: AuditLog) -> None:
        self._path = path
        self._store = store
        self._audit = audit
        self._items: dict[str, dict[str, Any]] = {
            item: {"status": UNKNOWN, "value": None, "reason": "never declared"} for item in ITEMS
        }
        self._load()
        self._publish()

    # ---- reading ----------------------------------------------------------

    def is_known(self, item: str) -> bool:
        return self._items[item]["status"] == KNOWN

    def value(self, item: str) -> Any:
        """The trusted value of an item. Only meaningful if is_known(item)."""
        return self._items[item]["value"]

    def describe(self, item: str) -> dict:
        return dict(self._items[item])

    # ---- changing ---------------------------------------------------------

    def begin_change(self, item: str) -> dict:
        """A command that changes this item is about to be sent.

        Returns the previous record, for restore() if the server refuses the
        command outright.
        """
        previous = dict(self._items[item])
        self._items[item] = {"status": CHANGING, "value": previous["value"], "reason": None}
        self._save()
        return previous

    def restore(self, item: str, previous: dict) -> None:
        self._items[item] = dict(previous)
        self._save()

    def set_known(self, item: str, value: Any) -> None:
        self._items[item] = {"status": KNOWN, "value": value, "reason": None}
        self._save()

    def set_unknown(self, item: str, reason: str) -> None:
        if self._items[item]["status"] == UNKNOWN:
            return
        self._items[item] = {"status": UNKNOWN, "value": self._items[item]["value"], "reason": reason}
        self._audit.write("equipment_unknown", item=item, reason=reason)
        self._save()

    def declare(self, item: str, value: Any, *, user: str) -> None:
        """An operator states what is actually loaded."""
        before = self.describe(item)
        self._items[item] = {"status": KNOWN, "value": value, "reason": None}
        self._audit.write("equipment_declared", user=user, item=item, value=value, before=before)
        self._save()

    # ---- persistence --------------------------------------------------------

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            # An unreadable record must not be trusted: everything stays unknown.
            for item in ITEMS:
                self._items[item]["reason"] = "the saved record could not be read"
            return
        for item in ITEMS:
            saved = data.get("items", {}).get(item)
            if not isinstance(saved, dict) or saved.get("status") not in (KNOWN, CHANGING, UNKNOWN):
                continue
            if saved["status"] == CHANGING:
                saved = {
                    "status": UNKNOWN,
                    "value": saved.get("value"),
                    "reason": "the backend stopped while a command was changing it",
                }
            self._items[item] = {
                "status": saved["status"],
                "value": saved.get("value"),
                "reason": saved.get("reason"),
            }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"items": self._items}, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._path)
        self._publish()

    def _publish(self) -> None:
        self._store.publish(
            EQUIPMENT_KEY,
            {"items": {item: dict(record) for item, record in self._items.items()}, "ts": time.time()},
        )
