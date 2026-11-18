"""E-mails to the operators.

Two ways to send them:

- Straight to a mail server (SMTP), when ROBOTWEB_SMTP_HOST is set. The
  account name and password come from the settings file and are never logged.
- Otherwise through the control server: `SCHEDULER SEND_MESSAGE <address>
  <subject> <text>`, as the older C# client did. The subject is one word.

The addresses come from the setting ROBOTWEB_OPERATOR_EMAILS (comma
separated); they are personal data and are kept out of the repository and
out of the run log.

To try the settings, send one message by hand (from the backend folder):

    python -m robotweb.notify
"""

import asyncio
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from .control.client import ControlConnectionError
from .control.protocol import ProtocolError
from .runner import CommandFailed, CommandRunner

_ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MAX_TEXT = 400
SECURITY = ("starttls", "ssl", "none")


class NotifyError(Exception):
    pass


def parse_addresses(setting: str) -> list[str]:
    addresses = [part.strip() for part in setting.split(",") if part.strip()]
    for address in addresses:
        if not _ADDRESS.match(address):
            raise NotifyError("ROBOTWEB_OPERATOR_EMAILS holds something that is not an e-mail address.")
    return addresses


@dataclass(frozen=True)
class Smtp:
    """How to reach the mail server. `password` is a secret: repr leaves it out."""

    host: str
    port: int
    sender: str
    security: str = "starttls"
    user: str = ""
    password: str = ""
    timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if self.security not in SECURITY:
            raise NotifyError(f"ROBOTWEB_SMTP_SECURITY must be one of: {', '.join(SECURITY)}.")
        if not _ADDRESS.match(self.sender):
            raise NotifyError("ROBOTWEB_SMTP_FROM must be an e-mail address.")
        if self.user and self.security == "none":
            # A password must never cross the network unencrypted.
            raise NotifyError("ROBOTWEB_SMTP_USER needs ROBOTWEB_SMTP_SECURITY starttls or ssl.")

    def __repr__(self) -> str:
        return f"Smtp({self.host}:{self.port}, {self.security})"

    def deliver(self, addresses: list[str], subject: str, text: str) -> None:
        """Send one message to all addresses. Blocks; call it in a thread."""
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(addresses)
        message["Subject"] = subject
        message.set_content(text)
        context = ssl.create_default_context()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_s, context=context)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout_s)
        with server:
            if self.security == "starttls":
                server.starttls(context=context)
            if self.user:
                server.login(self.user, self.password)
            server.send_message(message)


class Notifier:
    def __init__(
        self,
        runner: CommandRunner | None,
        addresses: list[str],
        smtp: Smtp | None = None,
        problem: str = "",
    ) -> None:
        """`problem`: what is wrong with the mail settings. Nothing is sent
        then, and every attempt reports it."""
        self._runner = runner
        self.addresses = addresses
        self.smtp = smtp
        self.problem = problem

    @property
    def route(self) -> str:
        return "the mail server" if self.smtp is not None else "the control server"

    async def send(self, subject: str, text: str) -> None:
        """Raises NotifyError if the message could not be handed over."""
        if self.problem:
            raise NotifyError(f"the mail settings are wrong: {self.problem}")
        text = " ".join(text.split())
        if self.smtp is not None:
            try:
                await asyncio.to_thread(self.smtp.deliver, self.addresses, subject.replace("_", " ").strip(), text)
            except (OSError, smtplib.SMTPException) as exc:
                # The exception text is the server's answer or a network
                # error; it does not hold the password.
                raise NotifyError(f"mail server {self.smtp.host}: {type(exc).__name__}: {exc}") from exc
            return
        if self._runner is None:
            raise NotifyError("no mail server and no control server to send through")
        subject = re.sub(r"\s+", "_", subject.strip())
        try:
            for address in self.addresses:
                await self._runner.ask(f"SCHEDULER SEND_MESSAGE {address} {subject} {text[:MAX_TEXT]}")
        except (CommandFailed, ControlConnectionError, ProtocolError) as exc:
            raise NotifyError(f"control server: {exc}") from exc


def smtp_from_settings(settings) -> Smtp | None:
    """The mail server of the settings, or None if none is set. The simulator
    never sends real e-mails."""
    if not settings.smtp_host or settings.simulation:
        return None
    password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
    return Smtp(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.smtp_from,
        security=settings.smtp_security.strip().lower(),
        user=settings.smtp_user,
        password=password,
    )


def from_settings(runner: CommandRunner, settings) -> Notifier:
    """The notifier of the backend. Wrong mail settings do not stop the
    backend from starting: no e-mail is sent, and the run log says why."""
    try:
        return Notifier(runner, parse_addresses(settings.operator_emails), smtp_from_settings(settings))
    except NotifyError as exc:
        return Notifier(runner, ["unusable"], problem=str(exc))


def _main() -> int:
    """Send one test message with the settings of this machine."""
    from .config import get_settings

    settings = get_settings()
    try:
        addresses = parse_addresses(settings.operator_emails)
        smtp = smtp_from_settings(settings)
    except NotifyError as exc:
        print(f"Settings: {exc}")
        return 1
    if smtp is None:
        print("No mail server is set (ROBOTWEB_SMTP_HOST). Nothing sent.")
        return 1
    if not addresses:
        print("No operator address is set (ROBOTWEB_OPERATOR_EMAILS). Nothing sent.")
        return 1
    notifier = Notifier(None, addresses, smtp)
    try:
        asyncio.run(notifier.send("BL22HT test message", "The BL22 robot client can send e-mails."))
    except NotifyError as exc:
        print(f"Not sent: {exc}")
        return 1
    print(f"Sent to {len(addresses)} address(es) through {smtp.host}:{smtp.port} ({smtp.security}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
