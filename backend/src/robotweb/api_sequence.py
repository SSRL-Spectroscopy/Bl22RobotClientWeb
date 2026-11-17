"""Endpoints for sequences: list, plan, start, pause, resume, abort."""

from fastapi import APIRouter, Depends, HTTPException, Request

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


@router.get("/sequences")
async def list_sequences() -> list[dict]:
    return [{"name": s.name, "label": s.label, "description": s.description} for s in SEQUENCES.values()]


@router.get("/sequences/{name}/plan")
async def plan(name: str, request: Request, session: Session = Depends(require_operator)) -> dict:
    """The steps the sequence would take if started now: as a tree
    ("outline") and as indented text ("steps")."""
    if name not in SEQUENCES:
        raise HTTPException(404, f"Unknown sequence {name!r}.")
    try:
        engine = _engine(request)
        return {"outline": engine.outline(name), "steps": engine.plan(name)}
    except SequenceError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sequences/{name}/start", dependencies=[Depends(same_origin)])
async def start(name: str, request: Request, session: Session = Depends(require_helm)) -> dict:
    if name not in SEQUENCES:
        raise HTTPException(404, f"Unknown sequence {name!r}.")
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
