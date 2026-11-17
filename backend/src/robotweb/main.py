"""FastAPI application. Run with:  uvicorn robotweb.main:app --port 8600

Run a single worker only: the backend owns the control connections and the
shared state (see docs/ARCHITECTURE.md).
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .api_auth import COOKIE_NAME, Presence
from .api_auth import router as auth_router
from .api_commands import router as commands_router
from .api_samples import router as samples_router
from .api_sequence import router as sequence_router
from .sequence import SequenceEngine
from .motor_rules import MotorRules
from .mqueue import MeasurementQueue
from .portal import PortalClient
from .audit import AuditLog
from .equipment import EquipmentState
from .runner import CommandRunner
from .auth import LoginThrottle, Sessions, UserStore
from .config import get_settings
from .control import ControlClient
from .control.poller import StatusPoller
from .epics import EpicsMonitor
from .helm import HelmManager
from .pvs import PVS
from .state import StateStore

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    store = StateStore(history_window_s=settings.history_window_s)
    app.state.settings = settings
    app.state.store = store

    users = UserStore(settings.users_file)
    sessions = Sessions(users, lifetime_s=settings.session_lifetime_s)
    audit = AuditLog(settings.audit_file)
    helm = HelmManager(
        store,
        audit,
        request_timeout_s=settings.helm_request_timeout_s,
        absence_grace_s=settings.helm_absence_grace_s,
    )
    presence = Presence()
    app.state.users = users
    app.state.sessions = sessions
    app.state.login_throttle = LoginThrottle()
    app.state.audit = audit
    app.state.helm = helm
    app.state.presence = presence
    audit.write("backend_started")

    equipment = EquipmentState(settings.equipment_file, store, audit)
    runner = CommandRunner(
        ControlClient(
            settings.control_host,
            settings.control_port,
            connect_timeout_s=settings.control_connect_timeout_s,
            response_timeout_s=settings.control_response_timeout_s,
            name="command",
        ),
        store,
        equipment,
        audit,
        motion_enabled=settings.motion_commands_enabled,
    )
    app.state.equipment = equipment
    app.state.runner = runner
    # A broken rule file stops the backend from starting: better than
    # running without the check.
    app.state.motor_rules = MotorRules.load(settings.motor_rules_dir)

    portal = PortalClient(settings.bl22_api_url, settings.bl22_api_key)
    app.state.portal = portal
    app.state.queue = MeasurementQueue(settings.queue_file, store, audit)
    sequences = SequenceEngine(runner, equipment, app.state.queue, store, audit, settings.data_dir / "runs")
    app.state.sequences = sequences

    async def helm_clock() -> None:
        while True:
            await asyncio.sleep(1.0)
            helm.tick(lambda user: presence.has_page(user) and sessions.user_has_session(user))

    tasks = [
        asyncio.create_task(store.run(), name="state-flush"),
        asyncio.create_task(helm_clock(), name="helm-clock"),
    ]

    if settings.status_poll_enabled:
        poller = StatusPoller(
            ControlClient(
                settings.control_host,
                settings.control_port,
                connect_timeout_s=settings.control_connect_timeout_s,
                response_timeout_s=settings.control_response_timeout_s,
                name="status",
            ),
            store,
            idle_interval_s=settings.status_poll_idle_s,
            busy_interval_s=settings.status_poll_busy_s,
            on_status=runner.observe_status,
        )
        tasks.append(asyncio.create_task(poller.run(), name="status-poll"))

    epics = None
    if settings.epics_enabled:
        epics = EpicsMonitor(store, PVS)
        epics.start()

    yield

    if epics is not None:
        epics.stop()
    await sequences.stop()
    await runner.stop()
    await app.state.portal.close()
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="RobotClientWeb", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(commands_router)
app.include_router(samples_router)
app.include_router(sequence_router)


@app.get("/api/health")
async def health() -> dict:
    """Backend liveness plus what the status poll last saw of the control server."""
    control = app.state.store.get("control") or {"connected": False, "detail": "not polled"}
    return {
        "backend": "ok",
        "simulation": app.state.settings.simulation,
        "control_server": {"connected": control["connected"], "detail": control.get("detail")},
    }


@app.get("/api/pvs")
async def pvs() -> list[dict]:
    """The PVs on the status page, with labels, units and which ones are plotted."""
    return [pv.as_dict() for pv in PVS]


@app.websocket("/api/ws")
async def live(websocket: WebSocket) -> None:
    """Live state for one browser: a snapshot, then batched updates.

    Open to everyone; Observers need no login. If the browser is logged in,
    the open socket also counts as that operator being present, which the
    helm rules use. The page reconnects after logging in or out.
    """
    state = websocket.app.state
    store: StateStore = state.store
    session = state.sessions.get(websocket.cookies.get(COOKIE_NAME))
    await websocket.accept()
    queue = store.subscribe()
    if session is not None:
        state.presence.joined(session.user)
    try:
        await websocket.send_json(store.snapshot())
        while True:
            message = await queue.get()
            if message is None:  # dropped for being too slow
                await websocket.close(code=1013)
                return
            await websocket.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        store.unsubscribe(queue)
        if session is not None:
            state.presence.left(session.user)


# Serve the built frontend, if there is one. Registered last so that it never
# shadows an /api route.
_dist = get_settings().frontend_dist
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
