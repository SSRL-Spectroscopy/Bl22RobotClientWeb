# RobotClientWeb

Web page for watching and controlling the BL22 experiment. The design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

- `backend/`: Python 3.12, FastAPI. Talks to the control server, the BL22 REST API and EPICS.
- `frontend/`: React, TypeScript, Vite.
- `reference/csharp/`: excerpts of the older C# client, for reference only.

## Backend

```powershell
cd backend
py -3.12 -m venv .venv          # or any Python 3.12 interpreter
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

### Starting and stopping

Start it in a PowerShell window (single worker only):

```powershell
cd C:\SSRL_LOCAL_OM\ai\RobotClientWeb\backend
$env:EPICS_CA_ADDR_LIST = [Environment]::GetEnvironmentVariable("EPICS_CA_ADDR_LIST","Machine")
.\.venv\Scripts\python.exe -m uvicorn robotweb.main:app --host 192.168.22.8 --port 8600 --log-level info
```

- `--host` is the address the page is served on. `192.168.22.8` is this computer on the beamline network, so other computers there reach the page at http://192.168.22.8:8600/. Without `--host` only this computer can open it, at http://127.0.0.1:8600/.
- `EPICS_CA_ADDR_LIST` tells EPICS where to ask for the PVs (for example `BL22:STORE`). The line above copies the value stored on the computer into the terminal; a terminal inside VS Code does not always have it. To give the addresses yourself, separated by spaces: `$env:EPICS_CA_ADDR_LIST = "ADDRESS1 ADDRESS2"`. The setting lasts for that window only.
- `--log-level` sets how much the window prints: `critical`, `error`, `warning`, `info` (the default, one line per request), `debug` or `trace`. The log files in `data/` are written the same at every level.

It is up when it prints `Uvicorn running on ...`. Keep the window open: closing it stops the backend.

Stop it with `Ctrl+C` in that window. Do this only when no sequence or command is running: what is loaded is "unknown" after a stop in the middle of a command. If the window is gone, stop the process that holds the port:

```powershell
Get-NetTCPConnection -LocalPort 8600 -State Listen | Select-Object OwningProcess
Stop-Process -Id NUMBER
```

Only one backend can use the port; a second start fails until the first is stopped. A restart is needed after a change to the backend code, `backend/.env` or the files in `config/`. It logs every operator out, frees the helm and switches the exchange log off. A change to the page needs only `npm run build` in `frontend` and a reload in the browser.

If `frontend/dist` exists (after `npm run build`), the backend serves the page itself. While running, the backend polls `GET_STATUS` on the control server about once a second and holds Channel Access monitors on the PVs in `backend/src/robotweb/pvs.py`.

Settings come from environment variables or `backend/.env`, prefixed with `ROBOTWEB_`. Copy `backend/.env.example` to `backend/.env` to start. Secrets such as the BL22 API key (`ROBOTWEB_BL22_API_KEY`) go only in that file, which git ignores; never put them in the code or in a file that is committed.

| Variable | Default | Meaning |
|---|---|---|
| `ROBOTWEB_CONTROL_HOST` | `127.0.0.1` | Control server address |
| `ROBOTWEB_CONTROL_PORT` | `5000` | Control server port (the live server on this machine uses 13000) |
| `ROBOTWEB_STATUS_POLL_ENABLED` | `true` | Poll the subsystem states |
| `ROBOTWEB_STATUS_POLL_IDLE_S` | `1.0` | Poll interval while everything is idle |
| `ROBOTWEB_STATUS_POLL_BUSY_S` | `0.5` | Poll interval while any subsystem is busy |
| `ROBOTWEB_EPICS_ENABLED` | `true` | Monitor the EPICS PVs |
| `ROBOTWEB_HISTORY_WINDOW_S` | `300` | Seconds of history kept for plotted PVs |
| `ROBOTWEB_MOTION_COMMANDS_ENABLED` | `false` | Allow commands that move hardware. Read-only queries work either way |
| `ROBOTWEB_DATA_DIR` | `data/` in the project folder | Where the accounts file, the audit log and the equipment record live |
| `ROBOTWEB_SESSION_LIFETIME_S` | `43200` | How long an operator stays logged in (12 hours) |
| `ROBOTWEB_COOKIE_SECURE` | `false` | Set to `true` once the site is served over HTTPS |
| `ROBOTWEB_HELM_REQUEST_TIMEOUT_S` | `60` | Control transfers if the holder does not answer a request in this time |
| `ROBOTWEB_HELM_ABSENCE_GRACE_S` | `30` | Control is freed if the holder has no page open for this long |

### Operator accounts

Observers need no account. Operators are created on the machine that runs the backend:

```powershell
.\.venv\Scripts\python -m robotweb.manage_users add NAME
.\.venv\Scripts\python -m robotweb.manage_users set-password NAME
.\.venv\Scripts\python -m robotweb.manage_users remove NAME
.\.venv\Scripts\python -m robotweb.manage_users list
```

The password is asked for at the prompt (at least 8 characters) and stored only as an argon2 hash in `data/users.json`. Changes take effect without a restart. Removing an account ends its sessions; changing a password does not log out browsers that are already logged in.

Every login, logout and helm action is appended to `data/audit.log`, one JSON object per line. Sessions and the helm are held in memory, so a backend restart logs everyone out and frees the helm.

The defaults point at the fake control server, for working without the real one:

```powershell
.\.venv\Scripts\python -m robotweb.control.fake_server --port 5000
```

### Looking at raw responses

This prints the raw bytes of a few read-only requests (nothing moves). It was used to confirm the message framing:

```powershell
.\.venv\Scripts\python tools\capture_raw.py HOST PORT
```

### Commands and the equipment record

Commands that move hardware are refused until `ROBOTWEB_MOTION_COMMANDS_ENABLED=true` is set and the backend is restarted. The commands and the rules for when each is allowed are in `backend/src/robotweb/commands.py`.

The backend records what is loaded (frame on the beam, reference foil, FY filter) in `data/equipment.json`. An item is "unknown" until an operator at the helm declares it on the page, and becomes unknown again when a command fails or when the sample robot is moved by anything other than this backend. Commands that depend on an unknown item are refused.

### Simulation

To try commands and sequences without moving anything on the beamline:

```powershell
.\.venv\Scripts\python -m robotweb.simulate          # page on http://127.0.0.1:8601/
.\.venv\Scripts\python -m robotweb.simulate --busy 10 # each simulated process takes 10 s
```

**A simulated measurement run writes to the portal in `backend/.env`:** frames are reported scheduled, loaded and returned, and measurements are set to `done` although no data is uploaded. Use it with the dev portal only.

The control server is simulated and hardware commands are switched on. The portal is the one in `backend/.env`; the simulated storage is filled from its slot list, so queued frames are found where the portal says, with the right barcodes. Accounts are copied from `data/` on the first start; the equipment record, queue and logs live in `data-sim/`. EPICS is off, so the plot is empty. The page shows a "Simulation" banner. It can run next to the real backend.

### Science files

`config/` holds the edge and fluorescence tables and two mappings that can be edited: `reference_foils.json` (which reference foil calibrates the mono for an element and edge) and `fy_filters.json` (which filter goes in front of the PIPS detector). They are read when the backend starts, and a mistake in one of them stops the start with a message naming the file. `config/motor_rules/` holds the motor interlock rules; keep it the same as the control server's copy.

### The measurement sequence

"Measure the queue" runs every queued measurement and reports progress to the portal; the steps are described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). It needs the crystal values (the "Monochromator crystal" panel: Reload reads the two PVs, Apply writes them). It waits while the SPEAR current is below 400 mA and goes on 10 minutes after it is above 480 mA.

To record the raw exchange with the control server (every request and answer, including the status polling), tick "Log the control server exchange" in the sequence view. It is off by default and after every backend restart; each time it is switched on a new file is started in `data/exchange/`. E-mail addresses are masked, and the log switches itself off when a file reaches 200 MB.

When the control server is stopped at a breakpoint it answers nothing, and after the usual wait (10 seconds; 3 minutes for an upload) the run stops with an error. For such a session the operator at the helm can tick "Wait up to 60 min for the control server" in the sequence view: a request then waits up to an hour for its answer and the run goes on when the control server does. After one minute without an answer the page shows a notice and the operators get an e-mail (only with a mail server in `backend/.env`), and another when the answer arrives. A control server that is closed is still noticed at once. While a request waits, the run cannot be paused or aborted. The switch is off after every backend restart; with it off nothing changes.

To have the operators e-mailed when a run stops, waits for beam or goes on again, add their addresses to `backend/.env`, comma separated: `ROBOTWEB_OPERATOR_EMAILS=first@example.org,second@example.org`. With a mail server in the same file (`ROBOTWEB_SMTP_HOST`, `_PORT`, `_SECURITY`, `_USER`, `_PASSWORD`, `_FROM`; see `backend/.env.example`) the backend sends them itself; without one the control server sends them. `python -m robotweb.notify` in `backend` sends one test message.

## Frontend

```powershell
cd frontend
npm install
npm run dev      # http://localhost:5173, forwards /api to the backend on port 8600
npm run build    # writes frontend/dist, which the backend then serves
```

## Not done yet

- TLS: the site runs over plain HTTP. Operator passwords and session cookies will cross the network in clear text until TLS is added.
