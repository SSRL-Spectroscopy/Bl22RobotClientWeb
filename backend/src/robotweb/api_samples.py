"""Endpoints for the sample list (from the portal) and the measurement queue."""

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .api_auth import require_helm, require_operator, same_origin
from .auth import Session
from .mqueue import MeasurementQueue, QueueError
from .portal import PortalClient, PortalError, PortalNotConfigured
from .samples import normalise_frame

router = APIRouter(prefix="/api")


class SelectionBody(BaseModel):
    measurement_ids: list[str]


class OrderBody(BaseModel):
    barcodes: list[str]


def _portal_failure(exc: PortalError) -> HTTPException:
    if isinstance(exc, PortalNotConfigured):
        return HTTPException(503, str(exc))
    if exc.status in (404, 409, 422):
        return HTTPException(exc.status, str(exc))
    return HTTPException(502, str(exc))


@router.get("/samples")
async def samples(request: Request, session: Session = Depends(require_operator)) -> dict:
    """Every frame that occupies an internal-storage slot, read from the portal now.

    The portal's own queue lists only frames that are locked and still have
    pending measurements. Operators may also want to measure a finished frame
    again, so this takes the slot overview and reads each frame.

    Operators only: the answer contains owner and sample names.
    """
    portal: PortalClient = request.app.state.portal
    try:
        slots = await portal.internal_storage()
        raws = await asyncio.gather(*(portal.frame(str(entry.get("barcode", ""))) for entry in slots))
        frames = [normalise_frame(raw) for raw in raws]
        frames.sort(key=lambda f: (f["container"] is None, f["container"] or 0, f["slot"] or 0))
    except PortalError as exc:
        raise _portal_failure(exc) from exc
    return {"frames": frames, "fetched": time.time()}


@router.put("/queue/frames/{barcode}", dependencies=[Depends(same_origin)])
async def queue_set_frame(
    barcode: str, body: SelectionBody, request: Request, session: Session = Depends(require_helm)
) -> dict:
    """Queue a frame with the chosen measurements, or change the choice."""
    state = request.app.state
    try:
        # Read the frame again: it may have changed since the list was loaded.
        frame = normalise_frame(await state.portal.frame(barcode))
    except PortalError as exc:
        raise _portal_failure(exc) from exc
    queue: MeasurementQueue = state.queue
    try:
        queue.set_frame(frame, body.measurement_ids, user=session.user)
    except QueueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.delete("/queue/frames/{barcode}", dependencies=[Depends(same_origin)])
async def queue_remove_frame(barcode: str, request: Request, session: Session = Depends(require_helm)) -> dict:
    try:
        request.app.state.queue.remove(barcode, user=session.user)
    except QueueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/queue/order", dependencies=[Depends(same_origin)])
async def queue_reorder(body: OrderBody, request: Request, session: Session = Depends(require_helm)) -> dict:
    try:
        request.app.state.queue.reorder(body.barcodes, user=session.user)
    except QueueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.post("/queue/clear", dependencies=[Depends(same_origin)])
async def queue_clear(request: Request, session: Session = Depends(require_helm)) -> dict:
    request.app.state.queue.clear(user=session.user)
    return {"ok": True}
