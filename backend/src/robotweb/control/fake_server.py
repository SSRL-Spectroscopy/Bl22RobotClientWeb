"""A stand-in for the control server, for tests and offline development.

It speaks the confirmed wire format (see protocol.py) and
simulates busy subsystems: a start command answers at once and the subsystem
reports the step name until the simulated duration has passed.

Run it standalone with:  python -m robotweb.control.fake_server [--port 5000]
"""

import argparse
import asyncio
import contextlib
from dataclasses import dataclass, field

SUBSYSTEMS = ("MOTOR", "SAMPLE", "BEAMLINE", "SCAN")

# start command -> state name reported by GET_STATUS while it runs
START_COMMANDS: dict[tuple[str, str], str] = {
    ("MOTOR", "MOVE"): "Move",
    ("SAMPLE", "GET_FRAME"): "GetFrame",
    ("SAMPLE", "RETURN_FRAME"): "ReturnFrame",
    ("SAMPLE", "PLACE"): "MoveSampleToBeam",
    ("SAMPLE", "CALIBRATE_FRAME"): "CalibrateFrame",
    ("SAMPLE", "GET_REF"): "GetReference",
    ("SAMPLE", "RETURN_REF"): "ReturnReference",
    ("SAMPLE", "GET_FILTER"): "GetFilter",
    ("SAMPLE", "RETURN_FILTER"): "ReturnFilter",
    ("BEAMLINE", "MOVE_MONO"): "MoveMono",
    ("BEAMLINE", "AUTO_GAS_FILL"): "AutoGasFill",
    ("BEAMLINE", "SET_SLIT_GAP"): "SetSlitGap",
    ("BEAMLINE", "AUTO_DETUNE"): "AutoDetune",
    ("BEAMLINE", "ALIGN_TABLE"): "AlignTable",
    ("BEAMLINE", "AUTO_GAIN"): "AutoGain",
    ("BEAMLINE", "COLLECT_OFFSETS"): "CollectOffsets",
    ("SCAN", "CALIBRATE_ENERGY"): "CalibrateEnergy",
    ("SCAN", "START_XAS_SCAN"): "XasScan",
    ("SCAN", "START_XAS_FY_SCAN"): "XasFyScan",
}

# Where the robot's first two joints and BOT_VERT are while it holds a frame
# in the beam, and while it is parked. BOT_VERT moves with the robot.
_HOLDING = (-60.0, 30.0, 215.0)
_PARKED = (90.0, 25.74494, 209.0)


@dataclass
class _Subsystem:
    state: str = "IDLE"
    error: str = "NO_ERROR"
    task: asyncio.Task | None = None


@dataclass
class FakeControlServer:
    host: str = "127.0.0.1"
    port: int = 0  # 0 = let the OS pick a free port
    busy_s: float = 0.3  # how long a start command keeps its subsystem busy
    # The reference foils SAMPLE LIST_REF reports.
    references: list = field(
        default_factory=lambda: "Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Zr Nb Mo Ta W Pt Au".split()
    )
    # (subsystem, verb) -> seconds, for start commands that take longer or shorter.
    slow: dict = field(default_factory=dict)
    # Send the response in two pieces with a pause, to exercise the reader.
    split_delay_s: float = 0.0
    mono_energy: float = 5966.002295
    # (subsystem, verb) pairs the server answers with ERROR.
    refuse: set = field(default_factory=set)
    # (container, slot) -> barcode on the frame stored there; the frame the
    # robot holds is what SCHEDULER GET_BARCODE reads.
    barcodes: dict = field(default_factory=dict)
    held: tuple | None = None
    # Motor positions and robot joints, as read from the real system at rest.
    positions: dict = field(
        default_factory=lambda: {
            "DET_X": 0.0,
            "DET_Y": 130.0,
            "SOL_VERT": 25.0,
            "PIPS_VERT": 132.0,
            "OPTIC_VERT": 73.0,
            "BOT_VERT": 209.0,
            "SOL_HOR": 0.0,
            "REF_PIVOT": 0.0,
        }
    )
    joints: list = field(default_factory=lambda: [90.0, 25.74494, -23.56151, 0.0, 87.81657, 0.0])
    # One line per container, one flag per slot, "1" = occupied.
    slots: list = field(
        default_factory=lambda: [
            "1 1 1 1 0 0 0 1 1 1 1 0",
            "1 1 1 1 1 1 0 1 1 1 1 0",
            "1 1 1 1 1 1 1 1 0 0 0 0",
        ]
    )
    requests: list[str] = field(default_factory=list)
    scans: int = 0
    _subsystems: dict[str, _Subsystem] = field(default_factory=dict)
    _server: asyncio.Server | None = None
    _writers: set = field(default_factory=set)

    async def start(self) -> None:
        self._subsystems = {name: _Subsystem() for name in SUBSYSTEMS}
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            # wait_closed() waits for every client connection, so drop them first.
            for writer in list(self._writers):
                writer.close()
            await self._server.wait_closed()
            self._server = None
        for sub in self._subsystems.values():
            if sub.task is not None:
                sub.task.cancel()

    async def __aenter__(self) -> "FakeControlServer":
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.stop()

    def fail(self, subsystem: str, error: str) -> None:
        """Make a subsystem report an error in GET_STATUS."""
        self._subsystems[subsystem].error = error

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        with contextlib.suppress(ConnectionError, asyncio.CancelledError):
            while line := await reader.readline():
                request = line.decode("ascii", errors="replace").strip()
                if not request:
                    continue
                self.requests.append(request)
                payload = self._encode(self._evaluate(request))
                if self.split_delay_s > 0 and len(payload) > 4:
                    cut = payload.index(b"\r") + 1  # after the first line
                    writer.write(payload[:cut])
                    await writer.drain()
                    await asyncio.sleep(self.split_delay_s)
                    writer.write(payload[cut:])
                else:
                    writer.write(payload)
                await writer.drain()
        self._writers.discard(writer)
        writer.close()

    def _encode(self, lines: list[str]) -> bytes:
        # Same bytes as the real server: lines separated by a bare CR, the
        # response ended by CRLF CRLF.
        return ("\r".join(lines) + "\r\n\r\n").encode("ascii")

    def _evaluate(self, request: str) -> list[str]:
        parts = request.split()
        if len(parts) < 2:
            return [f"ERROR: FakeServer.EvaluateRequest malformed request '{request}'"]
        subsystem, verb, args = parts[0], parts[1], parts[2:]
        head = f"SUCCESS {subsystem} {verb} "

        if (subsystem, verb) == ("ROBOT", "GET_JOINTS"):
            return [head, " ".join(f"{value:.6f}" for value in self.joints)]

        if (subsystem, verb) == ("MOTOR", "GET_POSITION") and len(args) == 1:
            if args[0] not in self.positions:
                return [f"ERROR: FakeServer.MOTOR unknown motor {args[0]}"]
            return [head, f"{self.positions[args[0]]:.6f}"]

        if (subsystem, verb) in (("SCHEDULER", "GET_PHOTO"), ("SCHEDULER", "SEND_MESSAGE")):
            return [head]

        if (subsystem, verb) == ("SCHEDULER", "GET_BARCODE"):
            return [head, self.barcodes.get(self.held, "000-000")]

        sub = self._subsystems.get(subsystem)
        if sub is None:
            return [f"ERROR: FakeServer.EvaluateRequest unknown subsystem {subsystem}"]

        if verb == "GET_STATUS":
            return [head, f"{sub.state} {sub.error}"]

        if (subsystem, verb) in self.refuse:
            return [f"ERROR: FakeServer.{subsystem} {verb} refused"]

        if (subsystem, verb) in START_COMMANDS:
            if sub.state != "IDLE":
                return [f"ERROR: FakeServer.{subsystem} busy with {sub.state}"]
            if verb == "GET_FRAME" and len(args) == 2:
                self.held = (int(args[0]), int(args[1]))
                self.joints[0], self.joints[1], self.positions["BOT_VERT"] = _HOLDING
            elif verb == "RETURN_FRAME":
                self.held = None
                self.joints[0], self.joints[1], self.positions["BOT_VERT"] = _PARKED
            elif (subsystem, verb) == ("MOTOR", "MOVE") and len(args) == 2:
                if args[0] not in self.positions:
                    return [f"ERROR: FakeServer.MOTOR unknown motor {args[0]}"]
                self.positions[args[0]] = float(args[1])
            elif (subsystem, verb) == ("BEAMLINE", "MOVE_MONO") and len(args) == 1:
                self.mono_energy = float(args[0])
            sub.state = START_COMMANDS[(subsystem, verb)]
            sub.task = asyncio.get_running_loop().create_task(
                self._finish(sub, self.slow.get((subsystem, verb), self.busy_s))
            )
            if verb in ("START_XAS_SCAN", "START_XAS_FY_SCAN") and args:
                self.scans += 1
                return [head, f"{args[0]}_{self.scans:03d}"]  # the name of the scan's data
            return [head]

        if (subsystem, verb) in (("SCAN", "POST_XAS_SCAN"), ("SCAN", "POST_XAS_FY_SCAN")):
            return [head]


        if (subsystem, verb) == ("SCAN", "GET_MONO_ENERGY"):
            return [head, f"{self.mono_energy:.6f}"]

        if (subsystem, verb) == ("BEAMLINE", "GET_GAIN"):
            return [head, "1x1e9_V/A 1x1e9_V/A 1x1e9_V/A 1x1e9_V/A"]

        if (subsystem, verb) == ("SAMPLE", "LIST_REF"):
            return [head, " ".join(self.references)]

        if (subsystem, verb) == ("SAMPLE", "GET_SAMPLE_STORAGE_SLOTS"):
            return [head, *self.slots]

        return [f"ERROR: FakeServer.EvaluateRequest unknown command {subsystem} {verb} {' '.join(args)}".rstrip()]

    async def _finish(self, sub: _Subsystem, busy_s: float) -> None:
        await asyncio.sleep(busy_s)
        sub.state = "IDLE"


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Fake control server for development")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--busy", type=float, default=3.0, help="seconds a start command stays busy")
    args = parser.parse_args()

    server = FakeControlServer(host=args.host, port=args.port, busy_s=args.busy)
    await server.start()
    print(f"fake control server listening on {server.host}:{server.port}")
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
