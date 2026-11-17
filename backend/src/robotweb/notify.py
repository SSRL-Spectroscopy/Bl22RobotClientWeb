"""E-mails to the operators.

The control server sends them: `SCHEDULER SEND_MESSAGE <address> <subject>
<text>`, as the older C# client did. The subject is one word. The addresses
come from the setting ROBOTWEB_OPERATOR_EMAILS (comma separated); they are
personal data and are kept out of the repository and out of the run log.
"""

import re

from .runner import CommandRunner

_ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
MAX_TEXT = 400


class NotifyError(Exception):
    pass


def parse_addresses(setting: str) -> list[str]:
    addresses = [part.strip() for part in setting.split(",") if part.strip()]
    for address in addresses:
        if not _ADDRESS.match(address):
            raise NotifyError("ROBOTWEB_OPERATOR_EMAILS holds something that is not an e-mail address.")
    return addresses


class Notifier:
    def __init__(self, runner: CommandRunner, addresses: list[str]) -> None:
        self._runner = runner
        self.addresses = addresses

    async def send(self, subject: str, text: str) -> None:
        """Raises what CommandRunner.ask raises if the control server does
        not take the message."""
        subject = re.sub(r"\s+", "_", subject.strip())
        text = " ".join(text.split())[:MAX_TEXT]
        for address in self.addresses:
            await self._runner.ask(f"SCHEDULER SEND_MESSAGE {address} {subject} {text}")
