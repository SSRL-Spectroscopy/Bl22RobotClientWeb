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
from .api_crystal import router as crystal_router
from .beam import BeamWatch, pv_reader
from .crystal import ChannelAccess, Crystal, CrystalError, Memory
from .notify import from_settings as notifier_from_settings
from .references import References
from .science import Science
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
from .control.exchange_log import ExchangeLog
from .control import ControlClient
from .control.poller import StatusPoller
from .epics import EpicsMonitor
from .helm import HelmManager
from .mono import MonoEnergy
from .pvs import PVS, page_list
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
    # A broken rule or table file stops the backend from starting: better
    # than running without the check.
    app.state.motor_rules = MotorRules.load(settings.motor_rules_dir)
    app.state.science = Science(settings.science_dir)
    # The raw exchange with the control server, written to a file while an
    # operator has it switched on.
    exchange = ExchangeLog(settings.data_dir / "exchange", store)
    app.state.exchange = exchange
    runner = CommandRunner(
        ControlClient(
            settings.control_host,
            settings.control_port,
            connect_timeout_s=settings.control_connect_timeout_s,
            response_timeout_s=settings.control_response_timeout_s,
            name="command",
            exchange=exchange,
        ),
        store,
        equipment,
        audit,
        motion_enabled=settings.motion_commands_enabled,
        rules=app.state.motor_rules,
    )
    app.state.equipment = equipment
    app.state.runner = runner
    crystal = Crystal(
        ChannelAccess() if settings.epics_enabled else Memory(),
        store,
        audit,
        simulated=not settings.epics_enabled,
    )
    app.state.crystal = crystal

    portal = PortalClient(settings.bl22_api_url, settings.bl22_api_key)
    app.state.portal = portal
    references = References(runner, store, equipment)
    app.state.references = references
    app.state.queue = MeasurementQueue(
        settings.queue_file,
        store,
        audit,
        order=app.state.science.ordered,
        reference=lambda m: app.state.science.reference_foil(m["element"], m["edge"])[0],
    )
    notifier = notifier_from_settings(runner, settings)
    if notifier.problem:
        log.error("no e-mails will be sent, the mail settings are wrong: %s", notifier.problem)
    # Without EPICS (tests, simulator) there is no SPEAR current to watch.
    beam = None
    if settings.epics_enabled:
        beam = BeamWatch(
            pv_reader(store, f"pv.{settings.beam_pv}"),
            store,
            low_ma=settings.beam_low_ma,
            ok_ma=settings.beam_ok_ma,
            settle_s=settings.beam_settle_s,
        )
    app.state.beam = beam
    sequences = SequenceEngine(
        runner,
        equipment,
        app.state.queue,
        store,
        audit,
        settings.data_dir / "runs",
        portal=portal,
        science=app.state.science,
        crystal=crystal,
        simulation=settings.simulation,
        beam=beam,
        notifier=notifier,
        references=references,
    )
    app.state.sequences = sequences

    async def helm_clock() -> None:
        while True:
            await asyncio.sleep(1.0)
            helm.tick(lambda user: presence.has_page(user) and sessions.user_has_session(user))

    async def read_crystal() -> None:
        # In the background: an unreachable IOC must not delay the start.
        with contextlib.suppress(CrystalError):
            await crystal.reload()

    tasks = [
        asyncio.create_task(read_crystal(), name="crystal-read"),
        # In the background: an unreachable control server must not delay the start.
        asyncio.create_task(references.refresh(), name="references-read"),
        asyncio.create_task(store.run(), name="state-flush"),
        asyncio.create_task(helm_clock(), name="helm-clock"),
    ]
    if beam is not None:
        tasks.append(asyncio.create_task(beam.run(), name="beam-watch"))

    if settings.status_poll_enabled:
        poller = StatusPoller(
            ControlClient(
                settings.control_host,
                settings.control_port,
                connect_timeout_s=settings.control_connect_timeout_s,
                response_timeout_s=settings.control_response_timeout_s,
                name="status",
                exchange=exchange,
            ),
            store,
            idle_interval_s=settings.status_poll_idle_s,
            busy_interval_s=settings.status_poll_busy_s,
            on_status=runner.observe_status,
        )
        tasks.append(asyncio.create_task(poller.run(), name="status-poll"))

    epics = None
    if settings.epics_enabled:
        mono = MonoEnergy(store, lambda: crystal.d)
        # The encoder does not report while the mono stands still: work the
        # energy out again when the crystal values arrive or change.
        crystal.on_change = mono.update
        epics = EpicsMonitor(store, PVS, on_update=mono.on_pv_update)
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
app.include_router(crystal_router)
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
    """What the page shows: PVs with labels, units and the plot each belongs
    to, and the mono energy the backend works out."""
    return page_list()


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
