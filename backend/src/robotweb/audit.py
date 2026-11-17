"""Append-only audit log: who did what, and when.

One JSON object per line, so the file can be read with any text tool and
filtered with a script. Passwords are never written here.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action: str, *, user: str | None = None, ip: str | None = None, **detail) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "action": action,
            "user": user,
            "ip": ip,
        }
        if detail:
            record["detail"] = detail
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        log.info("audit: %s", line)
