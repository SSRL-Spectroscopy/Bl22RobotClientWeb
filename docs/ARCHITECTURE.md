# RobotClientWeb architecture

A web page that shows the status of the BL22 experiment and lets one operator at a time control it. It replaces the sequence logic of the older C# client (`reference/csharp/`).

Status of this document: agreed design as of 2026-11-16. Nothing here is built yet unless the build order at the end says so.

## Constraints

- Local network only, fewer than 10 users.
- Two roles: Observer (no login, read-only) and Operator (login required).
- Local account management, no external identity service.
- No database server. Plain files; SQLite only if a later feature needs queries.
- No TLS for now (see Open items).
- Developed on Windows, deployed on Rocky Linux 9. Python 3.12 on both.

## Overview

```
Browser (React + TypeScript + Vite)
   |  WebSocket: live state, pushed to every browser
   |  HTTP POST: login, helm, commands, sequence control
   v
FastAPI backend (one process, one worker; also serves the built frontend)
   |- state store (in memory)  --> broadcasts changes to all browsers
   |- auth, sessions, helm
   |- equipment state (persisted to a file)
   |- sequence runner
   '- adapters, each a long-running asyncio task with reconnect
        |- control server: two TCP connections (status poll, commands)
        |- BL22 REST API (called directly)
        '- EPICS Channel Access monitors
```

The backend must run as a single process with a single worker, because it owns the socket connections, the current state, the helm and the running sequence.

Data flows one way: adapters update the state store, the store pushes to browsers. A command request returns only accepted or rejected; its effect appears through the state stream, so every viewer sees the same thing. Every value carries a connected flag and a timestamp, so stale data is never shown as live.

## Control server protocol

ASCII over TCP, strictly request/response.

- Request: one line `<SUBSYSTEM> <VERB> [args]`, ended by CRLF.
- Success: first line `SUCCESS <SUBSYSTEM> <VERB>`, followed by zero or more data lines.
- Failure: one line starting with `ERROR`, containing the server call chain and the reason.
- Subsystems: `MOTOR`, `SAMPLE`, `BEAMLINE`, `SCAN` (each with `GET_STATUS`), and `SCHEDULER`.
- `GET_STATUS` returns one data line `<state> <error>`. The state is `IDLE` or the name of the running step (for example `MoveSampleToBeam`). The error is `NO_ERROR` in either letter case; anything else is treated as a failure.
- A long-running process answers its start command at once with `SUCCESS` or `ERROR`, then runs on the server. The client waits 100 ms, then polls `GET_STATUS` until `IDLE`. Some processes run for many minutes (an XAS scan with 8 sweeps can take about half an hour).
- Most subsystems have no abort command.

Framing, confirmed against the live server on 2026-11-16 with `backend/tools/capture_raw.py`: lines inside a response are separated by a bare CR, and every response, success or failure, ends with CRLF CRLF. Some read-only requests take more than a second to answer (`SAMPLE GET_SAMPLE_STORAGE_SLOTS` took over 1.5 s).

The backend uses two connections: one polls the four `GET_STATUS` commands about once a second, the other carries commands. The browser never sends raw protocol strings; the backend exposes named actions and builds the strings itself. Commands are implemented one at a time as they are needed. The `SCHEDULER` commands are not used: the backend calls the BL22 REST API directly.

## EPICS

Channel Access only, through `aioca` (asyncio-native, wraps `libca`), with `pyepics` as the fallback. The EPICS code sits behind one adapter. Expected load is under 20 PVs at first and 100 to 200 later.

Initial PVs:

| PV | Unit | Meaning |
|---|---|---|
| `BL22:SCAN:MASTER:ADC1` | V | I_0 |
| `BL22:SCAN:MASTER:ADC2` | V | I_1 |
| `BL22:SCAN:MASTER:ADC3` | V | I_2 |
| `BL22:SCAN:MASTER:ADC4` | V | PIPS |
| `SPEAR:BeamCurrAvg` | mA | SPEAR current |
| `BL22:SCAN:MASTER.TRIG` | | DAQ trigger status |
| `BL22:SCAN:MASTER.RTME` | s | Remaining time |

## Authentication and roles

- Observers open the page and the WebSocket without logging in.
- Operators log in with a username and password and get an HttpOnly, SameSite=Strict session cookie that lasts 12 hours. Sessions live in memory, so a backend restart means logging in again.
- Users are stored in a local file (`data/users.json`) with argon2 password hashes and managed by a small command-line tool (`python -m robotweb.manage_users`).
- After five failed logins for a username within five minutes, that username is refused for one minute.
- State-changing requests from a page on another site are refused.
- Every command endpoint checks the role on the backend.
- Every operator action is written to an append-only audit log file.

## The helm

Only one operator controls the experiment at a time. A command is accepted only from a logged-in Operator who holds the helm.

- If the helm is free, a request is granted at once.
- The helm belongs to a user, not to a browser: an operator logged in on two machines has control on both.
- If it is held, the holder is asked to transfer or deny. With no answer after 60 seconds, control transfers automatically.
- Only one request can wait at a time. The requester can cancel it, and it is dropped if the requester leaves.
- Denying is unlimited; each deny is logged.
- The holder can release the helm; logging out releases it.
- If the holder has no open connection for 30 seconds, the helm is freed.
- Transfer is allowed while a process or sequence is running.
- Everyone sees who holds the helm and any pending request.
- After a backend restart nobody holds the helm.

## Equipment state

The control server does not record what is loaded, so the backend does. This guards against software and operator mistakes that could cause a physical collision. It is not a safety system, and the control server's own protection stays in place as a second layer.

- Tracked by the backend (no readback exists): the frame (in storage or on the beam, and which slot), the reference foil, the FY filter.
- Read back from the control server: motor positions (`SOL_VERT`, `PIPS_VERT`, `OPTIC_VERT`, `DET_X`, `DET_Y`), compared with the named positions within a tolerance. An encoder cross-check can be added later.
- Tracked state is written to a file on every change and survives a restart.
- Any item can be "unknown". It becomes unknown when a command fails or when the status poll sees activity the backend did not start. Motion commands that depend on an unknown item are blocked until an operator declares the actual state.
- Each motion command has preconditions that the backend checks before sending it.
- While a command runs, the items it changes are marked "changing" on disk before the command is sent, so a backend restart mid-command leaves them unknown.
- If the control server refuses a command outright, the previous state is kept. If contact is lost or the subsystem reports an error, the affected items become unknown.

### Commands implemented so far

One command runs at a time, and only while its subsystem is idle. Commands that move hardware are off unless `ROBOTWEB_MOTION_COMMANDS_ENABLED` is set.

| Command | Sent as | Allowed when |
|---|---|---|
| Get frame | `SAMPLE GET_FRAME c s` | no frame on the beam |
| Return frame | `SAMPLE RETURN_FRAME c s` (the slot it came from) | a frame is on the beam |
| Get reference | `SAMPLE GET_REF name` | no reference loaded, no frame on the beam |
| Return reference | `SAMPLE RETURN_REF name` | a reference is loaded, no frame on the beam |
| Get FY filter | `SAMPLE GET_FILTER name` | no filter loaded, no frame on the beam |
| Return FY filter | `SAMPLE RETURN_FILTER` | a filter is loaded, no frame on the beam |

There is only one robot. It moves frames, reference foils and FY filters, and it also holds the frame in the beam during a measurement. While it holds a frame it cannot move anything else. That is why the frame always goes back to storage before a reference or a filter is fetched or returned, and why those commands require "no frame on the beam" (confirmed 2026-11-16). After the exchange the robot is free to fetch the frame again.

Not implemented yet: motor moves, `SAMPLE PLACE`, `SAMPLE CALIBRATE_FRAME`, slits, mono and the other `BEAMLINE` and `SCAN` processes.

### Motors

- `MOTOR GET_POSITION <name>` returns a motor's position in engineering units (usually mm); `MOTOR GET_ENCODER <name>` returns the encoder position in counts. Both are for motors of type TRINAMIC. There are also EPICS motors, not relevant yet.
- `ROBOT GET_JOINTS` returns the six joint positions of the robot on one line, separated by spaces. `MOTOR GET_POSITION` returns one number. Both formats were read from the live server on 2026-11-17.
- Any logged-in operator can read all motor positions and the joints from the page; the result is shown to everyone.
- `config/motor_rules/*.json` holds a copy of the control server's interlock rules: for a motor to move within a given range, other motors (and the robot joints `JOINT_1`, `JOINT_2`) must be within given ranges. For example, `DET_Y` may only move if `DET_X` is at 170 to 230, `PIPS_VERT` and `SOL_VERT` are below 30, `BOT_VERT` is above 210 and the robot is away. The control server enforces these, but may have bugs, so the backend checks them as well (`backend/src/robotweb/motor_rules.py`).
- A rule applies when the motor's position and the target are inside the rule's ranges and the move goes in the rule's `direction` (-1 negative, 0 any, 1 positive). Every condition of every applicable rule must hold. If no rule covers the motor's position and the target at all, whatever the direction, the backend refuses the move with "current position or target outside of defined rules"; this catches readings just outside a range, such as -0.001. A move that is covered only by rules for the other direction is allowed, for example lowering `PIPS_VERT` (both decided 2026-11-17). `OPTIC_VERT`, `REF_PIVOT` and `SOL_HOR` have no conditions; their files only declare the travel range.
- The rule check is written and tested against the rule files, but not yet connected to any command, because no motor command is implemented yet. The copy in `config/motor_rules` must be kept the same as the control server's files.

## The portal (BL22 REST API)

The mail-in XAS portal is where users define what to measure. Its instrument API is described in `reference/instrument-api.md`. The backend calls it directly with an API key from `backend/.env` (`ROBOTWEB_BL22_API_URL`, `ROBOTWEB_BL22_API_KEY`). The key is never logged and never sent to browsers. Dev and production portals have separate databases and keys.

- **Sample list:** every frame that occupies an internal-storage slot, with its measurements, read from the portal on request (the slot overview, then one read per frame). This includes frames with nothing pending, so that finished measurements can be queued again; frames that are not locked, not declared safe or not in state `internal_storage` are shown but cannot be queued. It contains owner and sample names, so only logged-in operators can read it.
- **Measurement queue:** the operator at the helm chooses frames and, per frame, which pending measurements to run, and can reorder or remove frames. The frame is read again from the portal when it is queued. The queue is saved to `data/queue.json` and is shown to everyone, without owner or sample names.
- **Unknown values:** a plate type, technique, edge, detection or beam mode this software does not know makes that measurement "not measurable" instead of causing a failure.
- Containers and slots are numbered from 0 in both the portal and the control server.

## Sequences

The measurement sequence (`RunFrame_2` in the C# client) runs in the backend as a server-side task. It continues when browsers close or the helm changes hands.

- A sequence is a list of steps; a step is one command plus waiting for `IDLE`.
- Queue: built from the BL22 REST API, which knows the barcode in each slot and the measurements for each frame. The operator selects which frames and which measurements to run or skip. Items that have not started can be added, removed, skipped or reordered.
- Pause and abort take effect between two commands. The running command always finishes.
- Only one sequence runs at a time. Manual commands are blocked while it runs and allowed while it is paused.
- After an abort or an error everything stays in place. A cleanup sequence is built from the equipment state and shown as a step list; it runs only when an operator starts it.
- When a frame is fetched, its barcode is compared with the one the API lists for that slot. A mismatch stops that frame.
- Every step goes to a run log that is streamed to all browsers and written to a file.
- The science rules (energies, detune, slit sizes, gains) live in a separate module with unit tests. Helper functions are written from scratch, not ported. The detune factor applies the low-energy cap, which the C# code did not.
- If the backend restarts during a sequence, the sequence is lost and the operator recovers manually.

### Sequences implemented so far

The engine is in `backend/src/robotweb/sequence.py`. A sequence is a coroutine that does everything through a context object: commands (with their preconditions and equipment updates), read-only requests, the run log, and the checkpoint where pause and abort take effect. Before starting, the page shows the planned steps. Run logs are written to `data/runs/`.

| Sequence | What it does |
|---|---|
| Verify the queued frames | For each queued frame: get it, read its barcode (`SCHEDULER GET_BARCODE`), compare with the portal's barcode for that slot, return it. A mismatch is marked on the queue entry. |
| Clean up | Returns the frame, then the reference foil, then the FY filter, as far as they are loaded. Needs all three to be known. Started only by an operator. |

The measurement sequence itself (the `RunFrame_2` logic) is not written yet.

Division of work with the control server: the control server uploads the measured sweeps to the portal itself, because it knows how to retrieve the data after a scan. The web backend only reads the queue and reports frame state and measurement status.

### Simulation

`python -m robotweb.simulate` runs the whole backend against a simulated control server on port 8601, with hardware commands switched on. Nothing on the beamline moves. The simulated storage is filled from the portal's slot list, so queued frames are found in their slots with the right barcodes. Accounts, the equipment record, the queue and the logs are kept separately in `data-sim/`. EPICS is off. The page shows a "Simulation" banner. The real and the simulated backend can run side by side.

## Open items

- **TLS:** not used. Operator passwords and session cookies cross the network in clear text. The design stays ready for it: no hard-coded `http://` or `ws://` URLs, a configurable `Secure` cookie flag, and the option of a reverse proxy.
- **Camera:** a later version shows a network camera stream. Low latency matters more than image quality and bandwidth is not a constraint, so MJPEG or WebRTC, not HLS.
- **Slot verification sequence:** fetch each occupied frame, read its barcode, return it, and compare with the API. After version 1.
- **Loaded-item record:** in the longer term the control server is the better home for it, so that all clients share it.

## Build order

1. Project skeleton, fake control server for tests, socket client, one raw capture against the real server.
2. Live status page: the four subsystem states and the initial PVs.
3. Login, the helm, the audit log.
4. Manual commands, added as needed, with equipment state and preconditions.
5. Sequence runner with pause, abort and run log, then the measurement sequence.
