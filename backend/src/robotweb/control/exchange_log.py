"""The raw exchange with the control server, written to a file on request.

Off unless an operator switches it on (and off again after a backend
restart): with the status polling it grows by some ten lines a second. Each
time it is switched on a new file is started in data/exchange/, named by the
time:

    2026-11-18 13:05:10.123 status  > BEAMLINE GET_STATUS
    2026-11-18 13:05:10.131 status  < SUCCESS BEAMLINE GET_STATUS | IDLE no_error
    2026-11-18 13:05:12.004 command ! no answer: TimeoutError()

">" is a request, "<" the answer with its lines joined by " | ", "!" a
request that got no valid answer. The first word after the time says which
connection it was: "command" (commands and read-outs) or "status" (polling).

E-mail addresses are masked. When a file reaches `max_bytes` the log
switches itself off, so a forgotten switch cannot fill the disk.
"""

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from ..state import StateStore

log = logging.getLogger(__name__)

KEY = "exchange_log"
_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


class ExchangeLog:
    def __init__(self, directory: Path, store: StateStore | None = None, *, max_bytes: int = 200_000_000) -> None:
        self._directory = directory
        self._store = store
        self._max_bytes = max_bytes
        self._file: TextIO | None = None
        self._path: Path | None = None
        self._bytes = 0
        self._since: float | None = None
        self._note: str | None = None
        self._publish()

    @property
    def enabled(self) -> bool:
        return self._file is not None

    @property
    def path(self) -> Path | None:
        """The file being written, or the last one written."""
        return self._path

    def start(self) -> Path:
        """Start a new file. Does nothing if the log is on already."""
        if self._file is None:
            self._directory.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            self._path = self._directory / f"{now:%Y-%m-%d_%H-%M-%S}_exchange.log"
            self._file = self._path.open("a", encoding="utf-8", newline="\n")
            self._bytes = 0
            self._since = time.time()
            self._note = None
            self._publish()
        assert self._path is not None
        return self._path

    def stop(self, note: str | None = None) -> None:
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError:
            pass
        self._file = None
        self._since = None
        self._note = note
        self._publish()

    def sent(self, connection: str, command: str) -> None:
        self._write(connection, ">", command)

    def received(self, connection: str, raw: bytes | str) -> None:
        text = raw.decode("ascii", errors="replace") if isinstance(raw, bytes) else raw
        lines = [part.strip() for part in text.replace("\n", "\r").split("\r")]
        self._write(connection, "<", " | ".join(line for line in lines if line))

    def failed(self, connection: str, why: str) -> None:
        self._write(connection, "!", why)

    def _write(self, connection: str, mark: str, text: str) -> None:
        if self._file is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"{stamp} {connection:<7} {mark} {_ADDRESS.sub('<address>', text)}\n"
        try:
            self._file.write(line)
            self._file.flush()
        except OSError as exc:
            # A full or missing disk must never disturb the exchange itself.
            log.error("exchange log: cannot write, switched off: %s", exc)
            self.stop(f"Switched off: the file could not be written ({exc}).")
            return
        self._bytes += len(line)
        if self._bytes >= self._max_bytes:
            self.stop(f"Switched off by itself: the file reached {self._max_bytes // 1_000_000} MB.")

    def _publish(self) -> None:
        if self._store is not None:
            self._store.publish(
                KEY,
                {
                    "enabled": self.enabled,
                    "file": self._path.name if self._path else None,
                    "since": self._since,
                    "note": self._note,
                    "ts": time.time(),
                },
            )
