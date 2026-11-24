# RobotClientWeb architecture

A web page that shows the status of the BL22 experiment and lets one operator at a time control it. It replaces the sequence logic of the older C# client (`reference/csharp/`).

Status of this document: agreed design as of 2026-11-16. Nothing here is built yet unless the build order at the end says so.

## Constraints

- Local network only, fewer than 10 users.
- Two roles: Observer (no login, read-only) and Operator (login required).
- Local account management, no external identity service.
- No database server. Plain files; SQLite only if a later feature needs queries.
- TLS with a self-signed certificate, switched on at the start command (see Open items).
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
| `BL22:SCAN:MASTER.RTME` | s | DAQ RTME (remaining time) |
| `BL22:SCAN:MASTER.TRIG` | | DAQ TRIGGER (state name) |
| `XSP3_7Chan:det1:DetectorState_RBV`, `StatusMessage_RBV` | | XSPRESS status and its message |
| `BL22:SCAN:MASTER:MOTOR1.REP` | counts | Mono encoder (raw) |
| `BL22:SCAN:MASTER:MOTOR1.UREV`, `.EREV` | | Units and encoder counts per revolution (constant) |
| `BL22:STORE:CRYSTAL_ENCODER_OFFSET` | deg | Encoder offset; changes at every energy calibration |

The crystal PVs (`BL22:STORE:...`) are served from 192.168.22.5:6064, which must be in `EPICS_CA_ADDR_LIST` of the process that runs the backend.

### Mono energy

The page plots the mono energy. The backend works it out from the encoder (`backend/src/robotweb/mono.py`):

    Bragg angle [deg] = REP x (UREV / EREV) + offset
    energy [eV]       = 12398.42 / (2 d sin(angle))

`d` is the lattice spacing from `BL22:STORE:CRYSTAL_D`, as last read by the crystal panel. The value is for display only; sequences ask the control server for the mono energy.

## The page

Desktop monitors only.

- **Page colour.** Amber while this window has control, pale blue otherwise. Red stays reserved for errors.
- **Top bar, always visible.** Connection and subsystem status, who has control with the button to take, request or release it, and the login. Below that two rolling plots that share one time window: the ion chambers and PIPS (three quarters of the width) and the mono energy. Two tiles show the SPEAR current and the remaining scan time. A request for control from another operator, the simulation notice and a lost connection appear as strips in the bar.
- **Side tabs.** Overview (the sequence with its step tree, and the log next to it), Queue (frames in internal storage and the measurement queue next to each other, each with its own scroll bar), Manual control (single commands and read-outs), Settings (the crystal and the record of what is loaded).

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

Motor moves, `SAMPLE PLACE`, `SAMPLE CALIBRATE_FRAME` and the `BEAMLINE` and `SCAN` processes (gas fill, slits, detune, table alignment, gain, offsets, mono move, energy calibration, the two XAS scans) are defined in the same file as commands that only sequences may send. They are not offered on the page and are refused when sent by hand.

### Motors

- `MOTOR GET_POSITION <name>` returns a motor's position in engineering units (usually mm); `MOTOR GET_ENCODER <name>` returns the encoder position in counts. Both are for motors of type TRINAMIC. There are also EPICS motors, not relevant yet.
- `ROBOT GET_JOINTS` returns the six joint positions of the robot on one line, separated by spaces. `MOTOR GET_POSITION` returns one number. Both formats were read from the live server on 2026-11-17.
- Any logged-in operator can read all motor positions and the joints from the page; the result is shown to everyone.
- `config/motor_rules/*.json` holds a copy of the control server's interlock rules: for a motor to move within a given range, other motors (and the robot joints `JOINT_1`, `JOINT_2`) must be within given ranges. For example, `DET_Y` may only move if `DET_X` is at 170 to 230, `PIPS_VERT` and `SOL_VERT` are below 30, `BOT_VERT` is above 210 and the robot is away. The control server enforces these, but may have bugs, so the backend checks them as well (`backend/src/robotweb/motor_rules.py`).
- A rule applies when the motor's position and the target are inside the rule's ranges and the move goes in the rule's `direction` (-1 negative, 0 any, 1 positive). Every condition of every applicable rule must hold. If no rule covers the motor's position and the target at all, whatever the direction, the backend refuses the move with "current position or target outside of defined rules"; this catches readings just outside a range, such as -0.001. A move that is covered only by rules for the other direction is allowed, for example lowering `PIPS_VERT` (both decided 2026-11-17). `OPTIC_VERT`, `REF_PIVOT` and `SOL_HOR` have no conditions; their files only declare the travel range.
- Every `MOTOR MOVE` the backend sends goes through the rule check first: the positions the motor's rules need are read, and the move is refused if a rule is not met. The copy in `config/motor_rules` must be kept the same as the control server's files.

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
| Clean up | Returns the frame, then the reference foil, then the FY filter, as far as they are loaded, and ends with the Soller slits and the PIPS parked (`SOL_VERT` and `PIPS_VERT` at 25; they are moved only if they are not there). Needs all three to be known. Can be started with nothing loaded, to park the two. Started only by an operator. |

| Measure the queue | Runs every queued measurement, frame by frame. See below. |

### The step tree

The page shows the steps of a sequence as a numbered tree (`backend/src/robotweb/outline.py`, `frontend/src/StepTree.tsx`): sections such as "Mn K: reference foil Mn, optimise the beamline" hold one line per command, request or report to the portal.

- **Plan.** Before the start the sequence is run once "dry": the same code that will run, but nothing is sent, and the queue, the equipment record and the portal are replaced by stand-ins. What it would go through is the plan, so the plan cannot drift from the run. The plan follows what would be loaded step by step, so it knows for example that the foil of the first edge has to go back before the second.
- **Run.** The run ticks the planned tree off. Each section and line gets its state (running, done, failed, stopped) and its start and end time; the page shows the elapsed time per line and per section and points at the running line. A section opens by itself while the run is inside it.
- **Differences.** The plan assumes the expected case: barcodes match, the mono is below 13 keV, the beam stays. Steps the run takes that were not planned are inserted where they happen; planned steps it passes over are shown as not needed.
- A sequence must reach the outside world only through its `Context`, and must give every request whose answer decides what happens next a `dry=` answer, or its plan will be wrong.

The tree is published under its own state key (`sequence_outline`) and stays on the page after the run, until the next sequence starts.

### The measurement sequence

Written from `RunFrame_2` in the C# client, in `backend/src/robotweb/measure.py`. Before starting, the page shows the plan: per frame and per element and edge, the reference foil, the detune factor and each measurement with its energy range, sweeps and scan time.

1. Every queued frame is reported to the portal as `scheduled`. `OPTIC_VERT` moves to the pipe position.
2. Per frame: get it (portal: `loaded`), read its barcode. A frame with the wrong barcode is returned and skipped.
3. The frame's measurements run grouped by element and edge (lowest edge energy first), within that by detection (transmission, TFY, SDD), then by beam (unfocused, focused).
4. For a new element or edge: the frame goes back to its slot while the robot exchanges the reference foil (and the FY filter); then the ion chambers are filled, the mono is detuned, the slits set, the table aligned, the gain set and the mono calibrated on the foil. The foil stays loaded for the next frame if that frame needs the same one, but the beamline is optimised at least once for every frame, against drift.
5. Once per frame the frame is aligned in the beam (at 10 keV if the mono is above 13 keV).
6. Per measurement: place the sample, raise the Soller slits and PIPS for TFY, take the photo, set slits and gain, collect offsets, drive the SDD in if needed, scan, and have the control server upload each sweep (`SCAN POST_XAS_SCAN` or `POST_XAS_FY_SCAN`).
7. The measurement is then reported `done` to the portal, in a step of its own, before the SDD moves back. A later version may evaluate the data in that step and run more sweeps.
8. When a frame is finished it is returned (portal: `internal_storage`). When the queue is finished the foil and the filter are returned.

Decisions (2026-11-17):

- A measurement that is already `done` or `error` in the portal can be queued. It is set back to `pending` just before its scan, so the upload starts a new batch.
- Any failure stops the whole run and leaves everything in place. Frames that were scheduled but never taken out of storage are released in the portal (`internal_storage`); the frame on the robot stays `loaded` until the cleanup sequence has returned it. The cleanup sequence first retracts and parks the SDD detector if it is not parked. A run that finishes returns the reference foil and the FY filter and ends with the Soller slits and the PIPS parked (`SOL_VERT` and `PIPS_VERT` at 25).
- No scan-info file is produced here: scan metadata belongs to the data upload, which is the control software's job. The "frame complete" e-mail comes from the portal. E-mails to the operators are described below.
- `BOT_VERT` moves with the robot: when the robot holds a frame in the beam, `BOT_VERT` is already in the range the detector rules need.
- `SAMPLE PLACE` takes `<plate> <side> <number> <fy>`, for example `8-CIRCLE LEFT 1 TRUE` (from `ParseApiSamplePosition_2`): sample A is 1, H is 8, and `<fy>` is `TRUE` for TFY and SDD detection. The plate types `8-circle`, `8-slot` and `8-circle-gas` are defined. The gas plate is the control server's `8-CIRCLE-AIR`; it spans the whole frame, so its side is `NONE` (`8-CIRCLE-AIR NONE 1 TRUE`), and its horizontal slit is 6 mm like `8-circle`. A queue with a sample on any other plate type is refused before anything moves.

### Reference foils the instrument has

`SAMPLE LIST_REF` lists the reference foils that are available (`backend/src/robotweb/references.py`). The backend reads it at start, whenever a frame is added to the queue, before the measurement sequence is planned or started, and on the "Read references" button.

- The queue names the foil each measurement needs. A measurement whose foil is not in the list is marked in the queue, and a line above the table says which foils are missing.
- The measurement sequence refuses to start while a queued measurement needs a missing foil.
- The foil that is loaded at the moment counts as available.
- If the list cannot be read (control server away, or one without the command), it is unknown: nothing is marked and the start is not refused.
- **Assumption, not yet checked against the control server:** the answer's data lines hold the names separated by spaces, commas or line breaks, written as `SAMPLE GET_REF` takes them. Names are compared without regard to case.

Single measurements can be removed from the queue; a frame whose last measurement goes is removed with it.

### Waiting for beam

`backend/src/robotweb/beam.py` watches the SPEAR current (`SPEAR:BeamCurrAvg`, already monitored for the status page). Decided 2026-11-17:

- Below 400 mA the beam counts as lost. The measurement sequence then waits before its next command; a command that is running finishes first.
- The run goes on once the current has stayed above 480 mA for 10 minutes. A reading at or below 480 mA in that time starts the 10 minutes again.
- If the current cannot be read for 60 s, that counts as lost beam as well.
- While waiting, the sequence stays "running" and the page shows why it waits. Abort works at once. Pause works too; while paused, commands can be sent by hand.
- A scan during which the beam was lost is finished and uploaded like any other. After the beam is back only the scan is repeated, and uploaded as the next batch with the sweep numbers continuing; nothing else of the measurement is done again. The beamline is not optimised again after a beam loss: the run goes on where it was.
- The operators get an e-mail when the beam is lost during a measurement run and another when it is restored, at once, whatever the run is doing.
- A command that fails because the beam is gone (for example the gain or the energy calibration) still stops the run like any other failure.
- Only the measurement sequence waits for beam; the verify and cleanup sequences do not need X-rays.

The limits are settings: `ROBOTWEB_BEAM_LOW_MA`, `ROBOTWEB_BEAM_OK_MA`, `ROBOTWEB_BEAM_SETTLE_S`, `ROBOTWEB_BEAM_PV`. With EPICS off (tests, simulator) there is no beam watch.

### E-mails to the operators

The backend sends them straight to a mail server (SMTP) when `ROBOTWEB_SMTP_HOST` is set in `backend/.env`; the account name and password are in the same file and are never logged, and a password is only sent over an encrypted connection. Without a mail server the control server sends them (`SCHEDULER SEND_MESSAGE <address> <subject> <text>`, as the C# client did). The simulator never uses the mail server. See `backend/src/robotweb/notify.py`. The measurement sequence sends one when it starts waiting for beam, when it goes on again, and when it stops on a failure. The addresses are in `ROBOTWEB_OPERATOR_EMAILS` in `backend/.env`, comma separated. They are not written to the run log. Without addresses nothing is sent and the run log says so. A message that cannot be sent is logged and never stops the run.

### Science rules

`backend/src/robotweb/science.py` works out everything a measurement needs before anything moves. It reads four files in `config/` when the backend starts:

| File | Content |
|---|---|
| `xray_edges.csv` | Absorption edge energies (X-Ray Data Booklet) |
| `xray_fluorescence_lines.csv` | Fluorescence lines per edge, for the SDD regions of interest |
| `reference_foils.json` | Measured element and edge to reference foil. An element not listed uses its own foil. Edit this file to add elements. |
| `fy_filters.json` | Measured element to the filter in front of the PIPS detector |

Edges are written as in the portal (`K`, `L1`, `L2`, `L3`) inside the backend and in the JSON files; the tables and the control server use `L-3`. The detune factor keeps the 0.45 cap at low energy, which the C# code overwrote. Eight sweeps per measurement.

### Monochromator crystal

Two PVs hold the crystal: `BL22:STORE:CRYSTAL_D` (lattice spacing in angstrom; about 3.13 is Si-111, about 1.92 is Si-220) and `BL22:STORE:CRYSTAL_PHI` (0 or 90 degrees). They are not monitored, because the crystal is changed by hand and rarely. The backend reads them at start and when an operator presses Reload, and writes them when the operator at the helm presses Apply (`backend/src/robotweb/crystal.py`). Sequences use the values last read; the measurement sequence refuses to start if they are unknown. With EPICS off (tests, simulator) the values are held in memory.

Division of work with the control server: the control server uploads the measured sweeps to the portal itself, because it knows how to retrieve the data after a scan. The web backend only reads the queue and reports frame state and measurement status.

### Simulation

`python -m robotweb.simulate` runs the whole backend against a simulated control server on port 8601, with hardware commands switched on. Nothing on the beamline moves. The simulated storage is filled from the portal's slot list, so queued frames are found in their slots with the right barcodes. Accounts, the equipment record, the queue and the logs are kept separately in `data-sim/`. EPICS is off, so the crystal values are held in memory. The page shows a "Simulation" banner. The portal is real: a simulated measurement run reports frame states and sets measurements to `done` in the configured portal without any data being uploaded, so point the simulator at the dev portal only. The real and the simulated backend can run side by side.

## Open items

- **TLS:** set up 2026-11-23 with a self-signed certificate (`backend/tools/make_cert.sh`, files in `data/tls`). uvicorn serves HTTPS itself when started with `--ssl-certfile` and `--ssl-keyfile`, and `ROBOTWEB_COOKIE_SECURE=true` makes the session cookie `Secure`; without these the site runs over plain HTTP as before. Nothing in the code depends on the scheme: no hard-coded `http://` or `ws://` URLs, and the cross-site check compares host names only. Still open: a certificate from SLAC IT for a host name, so that browsers need no manual trust, and on the Linux deployment a reverse proxy on port 443 (the audit log then needs the forwarded client address).
- **Camera:** built 2026-11-20 (`backend/src/robotweb/camera.py`). The backend reads the MJPEG stream of an AXIS camera over one connection and passes the newest picture on to every page (`/api/camera/stream`, shown in a box in the top bar). Low latency matters more than image quality and bandwidth is not a constraint, so nothing is buffered and a slow page skips pictures. The camera's credentials stay in the backend. A "Camera" page shows the picture large with pan, tilt and zoom controls (`POST /api/camera/ptz`, any logged-in operator, no helm needed). A move lasts only while the page keeps asking for it: the backend stops the camera about a second after the last request.
- **Slot verification sequence:** fetch each occupied frame, read its barcode, return it, and compare with the API. After version 1.
- **Loaded-item record:** in the longer term the control server is the better home for it, so that all clients share it.

## Build order

1. Project skeleton, fake control server for tests, socket client, one raw capture against the real server.
2. Live status page: the four subsystem states and the initial PVs.
3. Login, the helm, the audit log.
4. Manual commands, added as needed, with equipment state and preconditions.
5. Sequence runner with pause, abort and run log, then the measurement sequence.
