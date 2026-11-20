"""Endpoints for the camera: the picture stream and turning it (see camera.py)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .api_auth import require_operator, same_origin
from .auth import Session
from .camera import MEDIA_TYPE, Camera, CameraError, part

router = APIRouter(prefix="/api")


@router.get("/camera/stream")
async def stream(request: Request) -> StreamingResponse:
    """The camera picture as an endless MJPEG stream, for an <img> on the
    page. Open to everyone, like the rest of the status: Observers need no
    login."""
    camera: Camera = request.app.state.camera
    if not camera.configured:
        raise HTTPException(404, "No camera is configured.")

    async def parts():
        async for frame in camera.frames():
            yield part(frame)

    return StreamingResponse(parts(), media_type=MEDIA_TYPE, headers={"Cache-Control": "no-store"})


class PtzBody(BaseModel):
    action: Literal["move", "zoom", "stop", "home", "preset", "center"]
    # move: speeds from -100 to 100, right and up positive
    pan: float = Field(0, ge=-100, le=100)
    tilt: float = Field(0, ge=-100, le=100)
    # zoom: speed from -100 to 100, in positive
    speed: float = Field(0, ge=-100, le=100)
    # preset: its name
    name: str = Field("", max_length=100)
    # center: the point of the picture, 0 to 1 from the left and from the top
    x: float = Field(0.5, ge=0, le=1)
    y: float = Field(0.5, ge=0, le=1)


@router.post("/camera/ptz", dependencies=[Depends(same_origin)])
async def ptz(body: PtzBody, request: Request, session: Session = Depends(require_operator)) -> dict:
    """Turn or zoom the camera. Any operator who is logged in may: the camera
    is not part of the experiment, so this does not need the helm.

    "move" and "zoom" go on only while the page keeps sending them; the
    backend stops the camera by itself shortly after the last one."""
    camera: Camera = request.app.state.camera
    if not camera.configured:
        raise HTTPException(404, "No camera is configured.")
    try:
        if body.action == "move":
            await camera.move(body.pan, body.tilt)
        elif body.action == "zoom":
            await camera.zoom(body.speed)
        elif body.action == "stop":
            await camera.stop()
        elif body.action == "home":
            await camera.home()
        elif body.action == "preset":
            await camera.goto(body.name)
        else:
            await camera.center(body.x, body.y)
    except CameraError as exc:
        raise HTTPException(502, f"The camera was not moved: {exc}.") from exc
    if body.action in ("home", "preset"):
        # The moves by hand are not written down: there would be one line for
        # every moment a button is held.
        request.app.state.audit.write(f"camera_{body.action}", user=session.user, name=body.name or None)
    return {"ok": True}


@router.post("/camera/presets", dependencies=[Depends(same_origin)])
async def presets(request: Request, session: Session = Depends(require_operator)) -> dict:
    """Ask the camera again for its preset positions."""
    camera: Camera = request.app.state.camera
    if not camera.configured:
        raise HTTPException(404, "No camera is configured.")
    await camera.read_presets()
    return {"ok": True}
