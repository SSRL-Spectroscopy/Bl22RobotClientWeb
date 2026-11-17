"""In-memory live state and its fan-out to browsers.

Adapters call publish() and add_sample(). A flush loop batches what changed
and hands one message to every subscriber (one per open WebSocket), so a fast
source cannot flood the browsers.
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any

log = logging.getLogger(__name__)

Entry = dict[str, Any]


class StateStore:
    def __init__(
        self,
        *,
        history_window_s: float = 300.0,
        flush_interval_s: float = 0.1,
        heartbeat_s: float = 2.0,
        subscriber_queue_size: int = 300,
    ) -> None:
        self.history_window_s = history_window_s
        self._flush_interval_s = flush_interval_s
        self._heartbeat_s = heartbeat_s
        self._queue_size = subscriber_queue_size

        self._state: dict[str, Entry] = {}
        self._history: dict[str, deque[tuple[float, float]]] = {}
        self._dirty: set[str] = set()
        self._pending_samples: dict[str, list[tuple[float, float]]] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._last_sent = 0.0

    # ---- written by adapters -------------------------------------------

    def publish(self, key: str, entry: Entry) -> None:
        """Set the current value of a key. Unchanged entries are not re-sent.

        The "ts" field is ignored when deciding whether an entry changed, so a
        source that keeps reporting the same value causes no traffic.
        """
        previous = self._state.get(key)
        self._state[key] = entry
        if previous is None or _without_ts(previous) != _without_ts(entry):
            self._dirty.add(key)

    def add_sample(self, key: str, t: float, value: float) -> None:
        """Append one point to the rolling history of a plotted key."""
        history = self._history.setdefault(key, deque())
        history.append((t, value))
        self._trim(history, t)
        self._pending_samples.setdefault(key, []).append((t, value))

    # ---- read by the web layer -----------------------------------------

    def get(self, key: str) -> Entry | None:
        return self._state.get(key)

    def snapshot(self) -> dict:
        """Everything a newly connected browser needs.

        Samples that arrived since the last flush are in this snapshot and
        will also be in the next update, so receivers must ignore points that
        are not newer than the last one they hold.
        """
        now = time.time()
        history = {}
        for key, points in self._history.items():
            self._trim(points, now)
            history[key] = {"t": [p[0] for p in points], "v": [p[1] for p in points]}
        return {
            "type": "snapshot",
            "now": now,
            "window_s": self.history_window_s,
            "state": dict(self._state),
            "history": history,
        }

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # ---- flush loop ------------------------------------------------------

    def flush(self) -> dict | None:
        """Build and send one update message if anything changed.

        Sends an empty heartbeat after heartbeat_s of silence so browsers can
        tell a quiet system from a dead connection.
        """
        now = time.time()
        if not self._dirty and not self._pending_samples:
            if now - self._last_sent < self._heartbeat_s:
                return None
        message = {
            "type": "update",
            "now": now,
            "changes": {key: self._state[key] for key in self._dirty},
            "samples": {
                key: {"t": [p[0] for p in pts], "v": [p[1] for p in pts]}
                for key, pts in self._pending_samples.items()
            },
        }
        self._dirty = set()
        self._pending_samples = {}
        self._last_sent = now
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # The browser is not keeping up. Drop it; it will reconnect
                # and start again from a fresh snapshot.
                self._subscribers.discard(queue)
                _drain(queue)
                queue.put_nowait(None)
        return message

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            self.flush()

    def _trim(self, history: deque, now: float) -> None:
        cutoff = now - self.history_window_s
        while history and history[0][0] < cutoff:
            history.popleft()


def _without_ts(entry: Entry) -> Entry:
    return {k: v for k, v in entry.items() if k != "ts"}


def _drain(queue: asyncio.Queue) -> None:
    while not queue.empty():
        queue.get_nowait()
