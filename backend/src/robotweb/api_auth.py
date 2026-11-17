"""Login, logout and helm endpoints, plus the access checks other endpoints use.

Every state-changing endpoint in the backend must depend on require_operator
or require_helm. Hiding a button in the page is not access control.
"""

import asyncio
from collections import Counter
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .auth import LoginThrottle, Session, Sessions, UserStore
from .helm import HelmError, HelmManager

COOKIE_NAME = "robotweb_session"


class Presence:
    """Which logged-in users currently have a page (a WebSocket) open."""

    def __init__(self) -> None:
        self._open: Counter[str] = Counter()

    def joined(self, user: str) -> None:
        self._open[user] += 1

    def left(self, user: str) -> None:
        self._open[user] -= 1
        if self._open[user] <= 0:
            del self._open[user]

    def has_page(self, user: str) -> bool:
        return self._open[user] > 0


# ---- access checks ---------------------------------------------------------


def current_session(request: Request) -> Session | None:
    sessions: Sessions = request.app.state.sessions
    return sessions.get(request.cookies.get(COOKIE_NAME))


def require_operator(request: Request) -> Session:
    session = current_session(request)
    if session is None:
        raise HTTPException(401, "Log in as an operator first.")
    return session


def require_helm(request: Request) -> Session:
    """For every endpoint that controls the experiment."""
    session = require_operator(request)
    helm: HelmManager = request.app.state.helm
    if not helm.is_holder(session.user):
        raise HTTPException(403, "You do not have control. Request the helm first.")
    return session


def same_origin(request: Request) -> None:
    """Refuse state-changing requests sent by a page from another site.

    The session cookie is SameSite=Strict already; this is a second check.
    """
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(403, "Cross-site request refused.")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---- endpoints -------------------------------------------------------------

router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    username: str
    password: str


class RespondBody(BaseModel):
    accept: bool


def _me(session: Session | None) -> dict:
    if session is None:
        return {"user": None, "role": "observer"}
    return {"user": session.user, "role": session.role}


@router.get("/me")
async def me(request: Request) -> dict:
    return _me(current_session(request))


@router.post("/login", dependencies=[Depends(same_origin)])
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    state = request.app.state
    users: UserStore = state.users
    throttle: LoginThrottle = state.login_throttle
    username = body.username.strip()

    wait = throttle.locked_for(username)
    if wait > 0:
        state.audit.write("login_refused_locked", user=username, ip=_client_ip(request))
        raise HTTPException(429, f"Too many failed attempts. Try again in {int(wait) + 1} seconds.")

    # Hashing is slow on purpose; keep it off the event loop.
    valid = await asyncio.to_thread(users.verify, username, body.password)
    if not valid:
        throttle.failed(username)
        state.audit.write("login_failed", user=username, ip=_client_ip(request))
        raise HTTPException(401, "Wrong username or password.")

    throttle.succeeded(username)
    session = state.sessions.create(username)
    settings = state.settings
    response.set_cookie(
        COOKIE_NAME,
        session.token,
        max_age=int(settings.session_lifetime_s),
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
    )
    state.audit.write("login", user=username, ip=_client_ip(request))
    return _me(session)


@router.post("/logout", dependencies=[Depends(same_origin)])
async def logout(request: Request, response: Response) -> dict:
    state = request.app.state
    session = state.sessions.end(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    if session is not None:
        state.audit.write("logout", user=session.user, ip=_client_ip(request))
        # Other browsers of the same user may still be logged in.
        if not state.sessions.user_has_session(session.user):
            state.helm.user_left(session.user)
    return _me(None)


def _helm_action(action) -> dict:
    try:
        action()
    except HelmError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/helm/request", dependencies=[Depends(same_origin)])
async def helm_request(request: Request, session: Session = Depends(require_operator)) -> dict:
    helm: HelmManager = request.app.state.helm
    return _helm_action(lambda: helm.request(session.user))


@router.post("/helm/respond", dependencies=[Depends(same_origin)])
async def helm_respond(body: RespondBody, request: Request, session: Session = Depends(require_operator)) -> dict:
    helm: HelmManager = request.app.state.helm
    return _helm_action(lambda: helm.respond(session.user, accept=body.accept))


@router.post("/helm/cancel", dependencies=[Depends(same_origin)])
async def helm_cancel(request: Request, session: Session = Depends(require_operator)) -> dict:
    helm: HelmManager = request.app.state.helm
    return _helm_action(lambda: helm.cancel(session.user))


@router.post("/helm/release", dependencies=[Depends(same_origin)])
async def helm_release(request: Request, session: Session = Depends(require_operator)) -> dict:
    helm: HelmManager = request.app.state.helm
    return _helm_action(lambda: helm.release(session.user))
