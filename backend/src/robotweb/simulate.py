"""Run the backend against a simulated control server.

    python -m robotweb.simulate [--port 8601] [--busy 3]

Everything behaves as on the real system (login, helm, queue, commands,
sequences, run log), but the robot and the motors are simulated: nothing on
the beamline moves, and commands that move hardware are switched on.

What is real and what is not:
- Control server: SIMULATED (an in-process fake that speaks the same protocol).
- Portal: the one configured in backend/.env. The simulated storage is filled
  from the portal's slot list, so queued frames are found where the portal
  says they are, with the right barcodes.
- EPICS PVs: off, so the plot stays empty.
- Accounts, equipment record, queue, logs: kept separately in data-sim/, so a
  simulated run never changes what the real backend believes is loaded.
"""

import argparse
import asyncio
import os
import shutil
import threading

import uvicorn

from .config import _REPO_ROOT, get_settings
from .control.fake_server import FakeControlServer
from .portal import PortalClient, PortalError

CONTAINERS, SLOTS = 3, 12


async def _storage_from_portal() -> tuple[list[str], dict] | None:
    """Slot flags and barcodes taken from the portal's slot overview."""
    settings = get_settings()
    portal = PortalClient(settings.bl22_api_url, settings.bl22_api_key)
    try:
        entries = await portal.internal_storage()
    except PortalError as exc:
        print(f"simulation: the portal's slot list is not available ({exc}); using a default storage")
        return None
    finally:
        await portal.close()
    flags = [["0"] * SLOTS for _ in range(CONTAINERS)]
    barcodes = {}
    for entry in entries:
        container, slot = entry.get("container"), entry.get("slot")
        if isinstance(container, int) and isinstance(slot, int) and 0 <= container < CONTAINERS and 0 <= slot < SLOTS:
            flags[container][slot] = "1"
            barcodes[(container, slot)] = str(entry.get("barcode", "")).upper()
    return [" ".join(row) for row in flags], barcodes


def _start_fake_server(busy_s: float, storage: tuple[list[str], dict] | None) -> FakeControlServer:
    """Run the fake control server on its own event loop in a daemon thread."""
    server = FakeControlServer(busy_s=busy_s)
    if storage is not None:
        server.slots, server.barcodes = storage
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True, name="fake-control-server").start()
    asyncio.run_coroutine_threadsafe(server.start(), loop).result(10)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the backend against a simulated control server")
    parser.add_argument("--port", type=int, default=8601, help="port of the simulated web page")
    parser.add_argument("--busy", type=float, default=3.0, help="seconds each simulated process takes")
    args = parser.parse_args()

    data_dir = _REPO_ROOT / "data-sim"
    data_dir.mkdir(exist_ok=True)
    # Same operators as the real backend (password hashes only), unless the
    # simulation already has its own accounts file.
    real_users = get_settings().users_file
    if real_users.exists() and not (data_dir / "users.json").exists():
        shutil.copy(real_users, data_dir / "users.json")

    server = _start_fake_server(args.busy, asyncio.run(_storage_from_portal()))

    # Environment variables take precedence over backend/.env.
    os.environ.update(
        ROBOTWEB_SIMULATION="true",
        ROBOTWEB_CONTROL_HOST=server.host,
        ROBOTWEB_CONTROL_PORT=str(server.port),
        ROBOTWEB_MOTION_COMMANDS_ENABLED="true",
        ROBOTWEB_EPICS_ENABLED="false",
        ROBOTWEB_DATA_DIR=str(data_dir),
    )
    print(f"SIMULATION: fake control server on {server.host}:{server.port}; nothing on the beamline moves")
    print(f"SIMULATION: open http://127.0.0.1:{args.port}/")
    uvicorn.run("robotweb.main:app", host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
