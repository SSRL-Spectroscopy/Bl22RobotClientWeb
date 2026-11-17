"""The helm: at most one operator controls the experiment at a time.

Rules (see docs/ARCHITECTURE.md):
- A free helm is granted on request.
- A held helm: the holder is asked to transfer or deny. With no answer before
  the deadline, control transfers automatically.
- The holder can release it. Logging out releases it.
- If the holder has no open page for the grace period, the helm is freed
  (and goes to a waiting requester, if there is one).
- Held in memory only: after a backend restart nobody holds the helm.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .audit import AuditLog
from .state import StateStore

HELM_KEY = "helm"


class HelmError(Exception):
    """The requested helm action is not possible in the current situation."""


@dataclass
class _Request:
    user: str
    deadline: float


class HelmManager:
    def __init__(
        self,
        store: StateStore,
        audit: AuditLog,
        *,
        request_timeout_s: float = 60.0,
        absence_grace_s: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._audit = audit
        self._request_timeout_s = request_timeout_s
        self._absence_grace_s = absence_grace_s
        self._clock = clock
        self.holder: str | None = None
        self._pending: _Request | None = None
        self._absent_since: float | None = None
        self._publish()

    @property
    def requester(self) -> str | None:
        return self._pending.user if self._pending else None

    def is_holder(self, user: str) -> bool:
        return self.holder == user

    # ---- actions by operators -------------------------------------------

    def request(self, user: str) -> None:
        if self.holder == user:
            raise HelmError("you already have control")
        if self.holder is None:
            self._grant(user, "helm_taken")
            return
        if self._pending is not None:
            if self._pending.user == user:
                raise HelmError("your request is already waiting")
            raise HelmError(f"{self._pending.user} is already waiting for control")
        self._pending = _Request(user, self._clock() + self._request_timeout_s)
        self._audit.write("helm_requested", user=user, holder=self.holder)
        self._publish()

    def respond(self, user: str, *, accept: bool) -> None:
        """The holder answers the waiting request."""
        if self.holder != user:
            raise HelmError("you do not have control")
        if self._pending is None:
            raise HelmError("nobody is waiting for control")
        requester = self._pending.user
        if accept:
            self._grant(requester, "helm_transferred", previous=user)
        else:
            self._pending = None
            self._audit.write("helm_denied", user=user, requester=requester)
            self._publish()

    def cancel(self, user: str) -> None:
        """The requester withdraws the waiting request."""
        if self._pending is None or self._pending.user != user:
            raise HelmError("you have no waiting request")
        self._pending = None
        self._audit.write("helm_request_cancelled", user=user)
        self._publish()

    def release(self, user: str) -> None:
        if self.holder != user:
            raise HelmError("you do not have control")
        self._free("helm_released", user)

    def user_left(self, user: str) -> None:
        """Called on logout: give up whatever part this user has in the helm."""
        if self._pending is not None and self._pending.user == user:
            self._pending = None
            self._audit.write("helm_request_cancelled", user=user, reason="logout")
            self._publish()
        if self.holder == user:
            self._free("helm_released", user, reason="logout")

    # ---- time-based rules -------------------------------------------------

    def tick(self, is_present: Callable[[str], bool]) -> None:
        """Apply the deadline and absence rules. Call about once a second.

        is_present(user) says whether the user is logged in and has a page open.
        """
        now = self._clock()

        if self._pending is not None and not is_present(self._pending.user):
            # Never hand control to someone who has left.
            self._audit.write("helm_request_cancelled", user=self._pending.user, reason="requester left")
            self._pending = None
            self._publish()

        if self.holder is not None:
            if is_present(self.holder):
                self._absent_since = None
            else:
                if self._absent_since is None:
                    self._absent_since = now
                if now - self._absent_since >= self._absence_grace_s:
                    self._free("helm_freed_holder_absent", self.holder)
                    return

        if self._pending is not None and now >= self._pending.deadline:
            self._grant(self._pending.user, "helm_auto_transferred", previous=self.holder)

    # ---- internals ----------------------------------------------------------

    def _grant(self, user: str, action: str, **detail) -> None:
        self.holder = user
        self._pending = None
        self._absent_since = None
        self._audit.write(action, user=user, **detail)
        self._publish()

    def _free(self, action: str, user: str, **detail) -> None:
        """The holder gives up or loses control; a waiting requester gets it."""
        self._audit.write(action, user=user, **detail)
        self.holder = None
        self._absent_since = None
        if self._pending is not None:
            self._grant(self._pending.user, "helm_taken", previous=user)
        else:
            self._publish()

    def _publish(self) -> None:
        self._store.publish(
            HELM_KEY,
            {
                "holder": self.holder,
                "request": (
                    {"user": self._pending.user, "deadline": self._pending.deadline}
                    if self._pending
                    else None
                ),
                "ts": self._clock(),
            },
        )
