"""Runtime settings, read from environment variables or a .env file.

Every setting is prefixed with ROBOTWEB_, for example ROBOTWEB_CONTROL_HOST.
"""

import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
# Settings and secrets for this machine. Never in version control.
DEFAULT_ENV_FILE = _REPO_ROOT / "backend" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROBOTWEB_", extra="ignore")

    # BL22 REST API. The key is a secret: it is never logged, never sent to
    # browsers, and lives only in the .env file or the service environment.
    bl22_api_url: str = ""
    bl22_api_key: SecretStr | None = None

    # Control server (string protocol over TCP). The default points at the
    # fake server started with `python -m robotweb.control.fake_server`.
    control_host: str = "127.0.0.1"
    control_port: int = 5000
    control_connect_timeout_s: float = 5.0
    control_response_timeout_s: float = 10.0

    # Status polling ("<SUBSYSTEM> GET_STATUS" for each subsystem).
    status_poll_enabled: bool = True
    status_poll_idle_s: float = 1.0  # while every subsystem is idle
    status_poll_busy_s: float = 0.5  # while any subsystem is busy

    # EPICS Channel Access monitors.
    epics_enabled: bool = True

    # Rolling history kept for plotted PVs.
    history_window_s: float = 300.0

    # The built frontend, served by the backend if the folder exists.
    frontend_dist: Path = _REPO_ROOT / "frontend" / "dist"

    # Accounts file and audit log. Not in version control.
    data_dir: Path = _REPO_ROOT / "data"

    # Operator sessions.
    session_lifetime_s: float = 12 * 3600
    # Set to true once the site is served over HTTPS, so the browser never
    # sends the session cookie over plain HTTP.
    cookie_secure: bool = False

    # The helm (see docs/ARCHITECTURE.md).
    helm_request_timeout_s: float = 60.0  # no answer from the holder: transfer
    helm_absence_grace_s: float = 30.0  # holder has no page open: free the helm

    # The motor interlock rule files (a copy of the control server's).
    motor_rules_dir: Path = _REPO_ROOT / "config" / "motor_rules"

    # Edge and fluorescence tables, reference foil and FY filter mappings.
    science_dir: Path = _REPO_ROOT / "config"

    # The beam watch (beam.py): the measurement sequence waits while the SPEAR
    # current is below beam_low_ma and goes on beam_settle_s after it is above
    # beam_ok_ma. Needs EPICS.
    beam_pv: str = "SPEAR:BeamCurrAvg"
    beam_low_ma: float = 400.0
    beam_ok_ma: float = 480.0
    beam_settle_s: float = 600.0

    # Who gets an e-mail when a run stops, waits for beam or goes on again.
    # Comma separated. Empty: no e-mails, the run log only.
    operator_emails: str = ""

    # The mail server the e-mails go through (notify.py). Without a host they
    # go through the control server instead. The password is a secret, like
    # the API key. Security: starttls (port 587), ssl (port 465) or none
    # (port 25, only without a user name).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_from: str = ""

    # True when this backend talks to the simulated control server
    # (python -m robotweb.simulate). The page then shows a clear banner.
    simulation: bool = False

    # Commands that move hardware. Off unless switched on deliberately;
    # read-only queries work either way.
    motion_commands_enabled: bool = False

    @property
    def queue_file(self) -> Path:
        return self.data_dir / "queue.json"

    @property
    def equipment_file(self) -> Path:
        return self.data_dir / "equipment.json"

    @property
    def users_file(self) -> Path:
        return self.data_dir / "users.json"

    @property
    def audit_file(self) -> Path:
        return self.data_dir / "audit.log"


def get_settings() -> Settings:
    # ROBOTWEB_ENV_FILE points at a different settings file (the tests use it
    # to make sure they never read this machine's real settings).
    return Settings(_env_file=os.environ.get("ROBOTWEB_ENV_FILE", DEFAULT_ENV_FILE))
