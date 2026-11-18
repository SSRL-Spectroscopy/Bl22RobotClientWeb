"""EPICS Channel Access monitors, published into the state store.

All EPICS code lives in this file, so the client library (aioca) can be
swapped without touching anything else. aioca is imported lazily so that the
rest of the backend and its tests run without Channel Access.
"""

import logging
import time
from collections.abc import Callable, Sequence

from .pvs import PvDef
from .state import StateStore

log = logging.getLogger(__name__)


class EpicsMonitor:
    def __init__(
        self,
        store: StateStore,
        pvs: Sequence[PvDef],
        *,
        connect_timeout_s: float = 5.0,
        on_update: Callable[[str], None] | None = None,
    ) -> None:
        """`on_update` is called with the PV name after each published update
        (also a disconnect), for values worked out from PVs."""
        self._store = store
        self._notify = on_update
        self._pvs = {pv.name: pv for pv in pvs}
        self._connect_timeout_s = connect_timeout_s
        self._subscriptions: list = []

    def start(self) -> None:
        """Subscribe to every PV. Must be called from the running event loop."""
        from aioca import DBR_CHAR_STR, FORMAT_TIME, camonitor

        now = time.time()
        for pv in self._pvs.values():
            self._store.publish(pv.key, _disconnected(None, now))
            self._subscriptions.append(
                camonitor(
                    pv.name,
                    self._on_update,
                    format=FORMAT_TIME,
                    # enum: the state name; text: an array of characters as one string
                    datatype=DBR_CHAR_STR if pv.text else str if pv.enum else None,
                    # Report every update for plotted PVs; for the others the
                    # latest value is enough.
                    all_updates=pv.plot,
                    notify_disconnect=True,
                    connect_timeout=self._connect_timeout_s,
                )
            )
        log.info("monitoring %d PVs", len(self._pvs))

    def stop(self) -> None:
        for subscription in self._subscriptions:
            subscription.close()
        self._subscriptions = []

    def _on_update(self, value) -> None:
        pv = self._pvs.get(value.name)
        if pv is None:
            return
        if not value.ok:
            # Disconnected, or never connected within the timeout.
            previous = self._store.get(pv.key)
            self._store.publish(pv.key, _disconnected(previous, time.time()))
            if self._notify is not None:
                self._notify(pv.name)
            return

        ts = float(getattr(value, "timestamp", None) or time.time())
        plain = _to_plain(value)
        self._store.publish(
            pv.key,
            {
                "connected": True,
                "value": plain,
                "severity": int(getattr(value, "severity", 0) or 0),
                "ts": ts,
            },
        )
        if pv.plot and isinstance(plain, (int, float)):
            self._store.add_sample(pv.key, ts, float(plain))
        if self._notify is not None:
            self._notify(pv.name)


def _disconnected(previous: dict | None, now: float) -> dict:
    previous = previous or {}
    return {
        "connected": False,
        # Keep the last value so the page can show it as stale.
        "value": previous.get("value"),
        "severity": previous.get("severity", 0),
        "ts": previous.get("ts", now),
    }


def _to_plain(value):
    """Turn an aioca value (a str, int, float or numpy subclass) into JSON data."""
    if isinstance(value, str):
        return str(value)
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else None
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)
