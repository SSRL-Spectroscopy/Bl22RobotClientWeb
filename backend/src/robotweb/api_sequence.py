"""Endpoints for sequences: list, plan, start, pause, resume, abort."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .api_auth import require_helm, require_operator, same_origin
from .auth import Session
from .sequence import SEQUENCES, SequenceEngine, SequenceError

router = APIRouter(prefix="/api")


def _engine(request: Request) -> SequenceEngine:
    return request.app.state.sequences


def _do(action) -> dict:
    try:
        action()
    except SequenceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


async def _read_references(name: str, request: Request) -> None:
    """The measurement sequence refuses to start while a reference foil it
    needs is missing, so it must judge by what the instrument has now."""
    if name == "measure" and not _engine(request).active:
        await request.app.state.references.refresh()


class ExchangeLogBody(BaseModel):
    enabled: bool


@router.post("/exchange-log", dependencies=[Depends(same_origin)])
async def exchange_log(body: ExchangeLogBody, request: Request, session: Session = Depends(require_operator)) -> dict:
    """Switch the log of the raw exchange with the control server on or off.
    It only records: any operator may switch it, also during a run."""
    exchange = request.app.state.exchange
    if body.enabled and not exchange.enabled:
        try:
            path = exchange.start()
        except OSError as exc:
            raise HTTPException(500, f"The log file could not be created: {exc}") from exc
        request.app.state.audit.write("exchange_log_on", user=session.user, file=path.name)
    elif not body.enabled and exchange.enabled:
        exchange.stop()
        request.app.state.audit.write("exchange_log_off", user=session.user)
    return {"enabled": exchange.enabled}


@router.get("/sequences")
async def list_sequences() -> list[dict]:
    return [{"name": s.name, "label": s.label, "description": s.description} for s in SEQUENCES.values()]


@router.get("/sequences/{name}/plan")
async def plan(name: str, request: Request, session: Session = Depends(require_operator)) -> dict:
    """The steps the sequence would take if started now: as a tree
    ("outline") and as indented text ("steps")."""
    if name not in SEQUENCES:
        raise HTTPException(404, f"Unknown sequence {name!r}.")
    await _read_references(name, request)
    try:
        engine = _engine(request)
        return {"outline": engine.outline(name), "steps": engine.plan(name)}
    except SequenceError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sequences/{name}/start", dependencies=[Depends(same_origin)])
async def start(name: str, request: Request, session: Session = Depends(require_helm)) -> dict:
    if name not in SEQUENCES:
        raise HTTPException(404, f"Unknown sequence {name!r}.")
    await _read_references(name, request)
    return _do(lambda: _engine(request).start(name, user=session.user))


@router.post("/sequence/pause", dependencies=[Depends(same_origin)])
async def pause(request: Request, session: Session = Depends(require_helm)) -> dict:
    return _do(lambda: _engine(request).pause(user=session.user))


@router.post("/sequence/resume", dependencies=[Depends(same_origin)])
async def resume(request: Request, session: Session = Depends(require_helm)) -> dict:
    return _do(lambda: _engine(request).resume(user=session.user))


@router.post("/sequence/abort", dependencies=[Depends(same_origin)])
async def abort(request: Request, session: Session = Depends(require_helm)) -> dict:
    return _do(lambda: _engine(request).abort(user=session.user))
