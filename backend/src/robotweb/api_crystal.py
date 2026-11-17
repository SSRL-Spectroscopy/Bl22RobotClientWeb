"""Endpoints for the monochromator crystal values (see crystal.py)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .api_auth import require_helm, require_operator, same_origin
from .auth import Session
from .crystal import Crystal, CrystalError

router = APIRouter(prefix="/api")


class CrystalBody(BaseModel):
    d: float
    phi: float


@router.post("/crystal/reload", dependencies=[Depends(same_origin)])
async def reload(request: Request, session: Session = Depends(require_operator)) -> dict:
    """Read the two PVs again. Changes nothing, so any operator may do it."""
    crystal: Crystal = request.app.state.crystal
    try:
        await crystal.reload()
    except CrystalError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True}


@router.post("/crystal/apply", dependencies=[Depends(same_origin)])
async def apply(body: CrystalBody, request: Request, session: Session = Depends(require_helm)) -> dict:
    """Write the two PVs."""
    state = request.app.state
    if state.sequences.active:
        raise HTTPException(409, "A sequence is running. The crystal values cannot be changed now.")
    try:
        await state.crystal.apply(body.d, body.phi, user=session.user)
    except CrystalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}
