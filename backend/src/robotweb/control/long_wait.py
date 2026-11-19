"""Waiting a long time for the control server, on request.

The control server is sometimes stopped at a breakpoint by a developer.
Nothing has failed then, but it answers nothing, and after the usual wait
(10 s) the backend gives up and the run stops.

With this switched on by an operator, a request on the command connection
waits up to `max_wait_s` (1 h) for its answer on the same connection, so the
run goes on when the server does. After `warn_after_s` (1 min) without an
answer the operators are told, once per request, and again when the answer
arrives. A connection that breaks (the server was closed) still fails at
once.

Off after every backend restart. The status polling does not use it: the
page keeps showing that the control server does not answer.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ..state import StateStore
from .exchange_log import _ADDRESS

log = logging.getLogger(__name__)

KEY = "long_wait"

Told = Callable[[str, float], Awaitable[None]]  # (request, seconds waited so far)


class LongWait:
    def __init__(
        self,
        store: StateStore | None = None,
        *,
        warn_after_s: float = 60.0,
        max_wait_s: float = 3600.0,
        on_silent: Told | None = None,
        on_answer: Told | None = None,
    ) -> None:
        self._store = store
        self.warn_after_s = warn_after_s
        self.max_wait_s = max_wait_s
        self.on_silent = on_silent
        self.on_answer = on_answer
        self.enabled = False
        self._waiting: dict | None = None
        self._tasks: set[asyncio.Task] = set()
        self._publish()

    def set(self, enabled: bool) -> None:
        """A request that is waiting already keeps the wait it started with."""
        self.enabled = enabled
        self._publish()

    def silent(self, command: str, since: float) -> None:
        """The request sent at `since` has had no answer for warn_after_s."""
        command = _ADDRESS.sub("<address>", command)
        self._waiting = {"command": command, "since": since}
        self._publish()
        log.warning("no answer from the control server to %r for %.0f s, still waiting", command, time.time() - since)
        self._tell(self.on_silent, command, time.time() - since)

    def answered(self, command: str, since: float) -> None:
        """The answer came after all."""
        self._ended()
        command = _ADDRESS.sub("<address>", command)
        log.warning("the control server answered %r after %.0f s", command, time.time() - since)
        self._tell(self.on_answer, command, time.time() - since)

    def gave_up(self) -> None:
        """No answer within max_wait_s, or the connection broke."""
        self._ended()

    def _ended(self) -> None:
        if self._waiting is not None:
            self._waiting = None
            self._publish()

    def _tell(self, told: Told | None, command: str, seconds: float) -> None:
        if told is None:
            return
        task = asyncio.ensure_future(told(command, seconds))
        self._tasks.add(task)
        task.add_done_callback(self._told)

    def _told(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("could not report the long wait", exc_info=task.exception())

    def _publish(self) -> None:
        if self._store is None:
            return
        self._store.publish(
            KEY,
            {
                "enabled": self.enabled,
                "warn_after_s": self.warn_after_s,
                "max_wait_s": self.max_wait_s,
                "waiting": self._waiting,
                "ts": time.time(),
            },
        )
