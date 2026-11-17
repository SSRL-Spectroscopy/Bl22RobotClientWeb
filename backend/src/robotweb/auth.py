"""Local operator accounts and login sessions.

Accounts live in a JSON file with argon2 password hashes and are managed with
`python -m robotweb.manage_users`. Sessions live in memory, so a backend
restart logs everyone out. Observers have no account and no session.
"""

import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ROLE_OPERATOR = "operator"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
MIN_PASSWORD_LENGTH = 8

_hasher = PasswordHasher()
# Verified against when the user does not exist, so that an unknown username
# takes as long to reject as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


class UserError(ValueError):
    """A username or password does not meet the rules, or the user is unknown."""


class UserStore:
    """The accounts file. Re-read whenever it changes on disk, so accounts
    added or removed with the command-line tool take effect without a restart."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: float | None = None
        self._users: dict[str, dict] = {}

    def _load(self) -> dict[str, dict]:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            self._mtime, self._users = None, {}
            return self._users
        if mtime != self._mtime:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._users = data.get("users", {})
            self._mtime = mtime
        return self._users

    def _save(self, users: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"users": users}, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self._path)
        self._mtime = None  # force a re-read

    def names(self) -> list[str]:
        return sorted(self._load())

    def exists(self, username: str) -> bool:
        return username in self._load()

    def role(self, username: str) -> str | None:
        user = self._load().get(username)
        return user["role"] if user else None

    def set_password(self, username: str, password: str, *, create: bool) -> None:
        if not USERNAME_PATTERN.fullmatch(username):
            raise UserError("username: 1 to 32 characters from A-Z a-z 0-9 _ . -")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise UserError(f"password must have at least {MIN_PASSWORD_LENGTH} characters")
        users = dict(self._load())
        if create and username in users:
            raise UserError(f"user {username!r} already exists")
        if not create and username not in users:
            raise UserError(f"user {username!r} does not exist")
        users[username] = {"role": ROLE_OPERATOR, "hash": _hasher.hash(password)}
        self._save(users)

    def remove(self, username: str) -> None:
        users = dict(self._load())
        if username not in users:
            raise UserError(f"user {username!r} does not exist")
        del users[username]
        self._save(users)

    def verify(self, username: str, password: str) -> bool:
        """Check a password. Takes tens of milliseconds; call it off the event loop."""
        user = self._load().get(username)
        try:
            _hasher.verify(user["hash"] if user else _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return False
        return user is not None


@dataclass
class Session:
    token: str
    user: str
    role: str
    expires: float


class Sessions:
    def __init__(self, users: UserStore, *, lifetime_s: float, clock=time.time) -> None:
        self._users = users
        self._lifetime_s = lifetime_s
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def create(self, username: str) -> Session:
        session = Session(
            token=secrets.token_urlsafe(32),
            user=username,
            role=self._users.role(username) or ROLE_OPERATOR,
            expires=self._clock() + self._lifetime_s,
        )
        self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        """The session for a cookie value, or None if it is unknown, expired,
        or its account has been removed."""
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if self._clock() >= session.expires or not self._users.exists(session.user):
            del self._sessions[token]
            return None
        return session

    def end(self, token: str | None) -> Session | None:
        return self._sessions.pop(token, None) if token else None

    def user_has_session(self, username: str) -> bool:
        return any(self.get(s.token) for s in list(self._sessions.values()) if s.user == username)


class LoginThrottle:
    """Slows down password guessing: after too many failures for a username,
    further attempts are refused for a while."""

    def __init__(self, *, max_failures: int = 5, window_s: float = 300, lock_s: float = 60, clock=time.time) -> None:
        self._max_failures = max_failures
        self._window_s = window_s
        self._lock_s = lock_s
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def locked_for(self, username: str) -> float:
        """Seconds until this username may try again; 0 if it may try now."""
        return max(0.0, self._locked_until.get(username.lower(), 0.0) - self._clock())

    def failed(self, username: str) -> None:
        key, now = username.lower(), self._clock()
        recent = [t for t in self._failures.get(key, []) if now - t < self._window_s]
        recent.append(now)
        if len(recent) >= self._max_failures:
            self._locked_until[key] = now + self._lock_s
            recent = []
        self._failures[key] = recent

    def succeeded(self, username: str) -> None:
        self._failures.pop(username.lower(), None)
        self._locked_until.pop(username.lower(), None)
