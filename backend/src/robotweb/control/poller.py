"""Polls "<SUBSYSTEM> GET_STATUS" on its own connection and publishes the result.

Runs all the time, whether or not a browser is open. It polls faster while any
subsystem is busy and slower when everything is idle.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from ..state import StateStore
from .client import ControlClient, ControlConnectionError
from .protocol import ProtocolError, SubsystemStatus, parse_status

log = logging.getLogger(__name__)

SUBSYSTEMS = ("MOTOR", "SAMPLE", "BEAMLINE", "SCAN")


def subsystem_key(name: str) -> str:
    return f"subsystem.{name}"


class StatusPoller:
    def __init__(
        self,
        client: ControlClient,
        store: StateStore,
        *,
        idle_interval_s: float = 1.0,
        busy_interval_s: float = 0.5,
        reconnect_delay_s: float = 2.0,
        subsystems: tuple[str, ...] = SUBSYSTEMS,
        on_status: Callable[[str, SubsystemStatus], None] | None = None,
    ) -> None:
        self._on_status = on_status
        self._client = client
        self._store = store
        self._idle_interval_s = idle_interval_s
        self._busy_interval_s = busy_interval_s
        self._reconnect_delay_s = reconnect_delay_s
        self._subsystems = subsystems
        self._was_connected: bool | None = None

    async def run(self) -> None:
        self._publish_disconnected("not polled yet")
        try:
            while True:
                delay = await self.poll_once()
                await asyncio.sleep(delay)
        finally:
            await self._client.close()

    async def poll_once(self) -> float:
        """Poll every subsystem once. Returns the delay until the next round."""
        any_busy = False
        try:
            for name in self._subsystems:
                response = await self._client.request(f"{name} GET_STATUS")
                now = time.time()
                try:
                    status = parse_status(response)
                except ProtocolError as exc:
                    # The server answered, but not with a usable status.
                    self._store.publish(
                        subsystem_key(name),
                        {
                            "connected": True,
                            "state": "UNKNOWN",
                            "error": str(exc),
                            "failed": True,
                            "ts": now,
                        },
                    )
                    continue
                any_busy = any_busy or not status.idle
                if self._on_status is not None:
                    self._on_status(name, status)
                self._store.publish(
                    subsystem_key(name),
                    {
                        "connected": True,
                        "state": status.state,
                        "error": status.error,
                        "failed": status.failed,
                        "ts": now,
                    },
                )
        except (ControlConnectionError, ProtocolError) as exc:
            if self._was_connected is not False:
                log.warning("status poll lost the control server: %s", exc)
            self._was_connected = False
            self._publish_disconnected(str(exc))
            return self._reconnect_delay_s

        if self._was_connected is not True:
            log.info("status poll is talking to the control server")
        self._was_connected = True
        self._store.publish("control", {"connected": True, "detail": None, "ts": time.time()})
        return self._busy_interval_s if any_busy else self._idle_interval_s

    def _publish_disconnected(self, detail: str) -> None:
        now = time.time()
        self._store.publish("control", {"connected": False, "detail": detail, "ts": now})
        for name in self._subsystems:
            previous = self._store.get(subsystem_key(name)) or {}
            self._store.publish(
                subsystem_key(name),
                {
                    "connected": False,
                    # Keep the last known values so the page can show them as stale.
                    "state": previous.get("state"),
                    "error": previous.get("error"),
                    "failed": previous.get("failed", False),
                    "ts": previous.get("ts", now),
                },
            )
