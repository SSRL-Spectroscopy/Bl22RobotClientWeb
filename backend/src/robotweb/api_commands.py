"""Endpoints for operator commands, read-only queries and equipment declarations."""

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .api_auth import require_helm, require_operator, same_origin
from .auth import Session
from .commands import COMMANDS, QUERIES, CommandRefused, clean_declaration
from .control.client import ControlConnectionError
from .control.protocol import ProtocolError
from .equipment import ITEMS
from .runner import CommandFailed, CommandRunner

router = APIRouter(prefix="/api")


class CommandBody(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class DeclareBody(BaseModel):
    item: str
    value: Any = None


@router.get("/commands")
async def list_commands(request: Request) -> dict:
    """What the page can offer. Open to everyone; sending needs the helm."""
    runner: CommandRunner = request.app.state.runner
    return {
        "motion_enabled": runner.motion_enabled,
        "commands": [command.describe() for command in COMMANDS.values()],
        "queries": [query.describe() for query in QUERIES.values()],
    }


@router.post("/commands/{name}", dependencies=[Depends(same_origin)])
async def send_command(
    name: str, body: CommandBody, request: Request, session: Session = Depends(require_helm)
) -> dict:
    if name not in COMMANDS:
        raise HTTPException(404, f"Unknown command {name!r}.")
    runner: CommandRunner = request.app.state.runner
    try:
        await runner.start(name, body.args, user=session.user)
    except CommandRefused as exc:
        request.app.state.audit.write(
            "command_refused", user=session.user, command=name, args=body.args, reason=str(exc)
        )
        raise HTTPException(409, str(exc)) from exc
    except (ControlConnectionError, ProtocolError) as exc:
        raise HTTPException(502, f"The control server did not answer: {exc}") from exc
    return {"ok": True}


@router.post("/queries/{name}", dependencies=[Depends(same_origin)])
async def run_query(name: str, request: Request, session: Session = Depends(require_operator)) -> dict:
    """Read-only requests. Any logged-in operator may send them."""
    if name not in QUERIES:
        raise HTTPException(404, f"Unknown query {name!r}.")
    runner: CommandRunner = request.app.state.runner
    try:
        return {"lines": await runner.query(name)}
    except CommandRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ControlConnectionError, ProtocolError) as exc:
        raise HTTPException(502, f"The control server did not answer: {exc}") from exc


@router.post("/motors/read", dependencies=[Depends(same_origin)])
async def read_motors(request: Request, session: Session = Depends(require_operator)) -> dict:
    """Read every motor that has interlock rules, and the robot joints.

    Read-only. The result is also published, so everyone sees it.
    """
    state = request.app.state
    names = set(state.motor_rules.motors) | {"JOINT_1"}
    try:
        positions = await state.runner.read_positions(names)
    except CommandFailed as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ControlConnectionError, ProtocolError) as exc:
        raise HTTPException(502, f"The control server did not answer: {exc}") from exc
    entry = {"positions": positions, "ts": time.time()}
    state.store.publish("motors", entry)
    return entry


@router.post("/equipment/declare", dependencies=[Depends(same_origin)])
async def declare(body: DeclareBody, request: Request, session: Session = Depends(require_helm)) -> dict:
    """The operator at the helm states what is actually loaded."""
    state = request.app.state
    if body.item not in ITEMS:
        raise HTTPException(404, f"Unknown item {body.item!r}.")
    if state.runner.busy:
        raise HTTPException(409, "Wait until the running command has finished.")
    try:
        value = clean_declaration(body.item, body.value)
    except CommandRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    state.equipment.declare(body.item, value, user=session.user)
    return {"ok": True}
