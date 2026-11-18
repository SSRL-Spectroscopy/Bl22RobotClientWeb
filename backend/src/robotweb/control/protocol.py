"""Wire format of the control server protocol.

Request:  one ASCII line "<SUBSYSTEM> <VERB> [args]" ended by CRLF.
Success:  first line "SUCCESS <SUBSYSTEM> <VERB> ", then zero or more data lines.
Failure:  one line starting with "ERROR".

Lines inside a response are separated by a bare CR. Every response, success or
failure, ends with CRLF CRLF. Confirmed against the live server on 2026-11-16
with tools/capture_raw.py, for example:

    b'SUCCESS SAMPLE GET_STATUS \\rIDLE NO_ERROR\\r\\n\\r\\n'
    b'ERROR: OmServer.HandleClient ... UNKNOWN_COMMAND\\r\\n\\r\\n'
"""

from dataclasses import dataclass

ENCODING = "ascii"
REQUEST_TERMINATOR = b"\r\n"
RESPONSE_TERMINATOR = b"\r\n\r\n"

NO_ERROR = "NO_ERROR"
IDLE = "IDLE"


class ProtocolError(Exception):
    """The server sent something that does not fit the protocol."""


@dataclass(frozen=True)
class Response:
    ok: bool
    lines: tuple[str, ...]  # data lines of a success; empty for a failure
    error: str | None  # the full error line of a failure
    raw: str

    @property
    def first(self) -> str:
        """First data line, for the many commands that return exactly one."""
        if not self.lines:
            raise ProtocolError(f"response carries no data: {self.raw!r}")
        return self.lines[0]


@dataclass(frozen=True)
class SubsystemStatus:
    state: str  # "IDLE" or the name of the running step
    error: str  # "NO_ERROR" (normalised to upper case) or the server's error word(s)

    @property
    def idle(self) -> bool:
        return self.state == IDLE

    @property
    def failed(self) -> bool:
        return self.error != NO_ERROR


def encode_request(command: str) -> bytes:
    command = command.strip()
    if not command:
        raise ValueError("empty command")
    if "\r" in command or "\n" in command:
        raise ValueError("a command must be a single line")
    return command.encode(ENCODING) + REQUEST_TERMINATOR


def parse_response(raw: bytes) -> Response:
    text = raw.decode(ENCODING, errors="replace")
    # Lines are separated by CR; stripping and dropping empty lines removes the
    # CRLF CRLF terminator.
    lines = [part.strip() for part in text.split("\r")]
    lines = [line for line in lines if line]
    if not lines:
        raise ProtocolError("empty response")

    head = lines[0]
    word = head.split(maxsplit=1)[0]
    if word == "SUCCESS":
        return Response(ok=True, lines=tuple(lines[1:]), error=None, raw=text)
    if word.startswith("ERROR"):
        return Response(ok=False, lines=(), error=" ".join(lines), raw=text)
    raise ProtocolError(f"response starts with neither SUCCESS nor ERROR: {text!r}")


def parse_status(response: Response) -> SubsystemStatus:
    """Interpret the answer to "<SUBSYSTEM> GET_STATUS": "<state> <error>".

    The name of a running step can be several words ("AutoGas Restore HV
    no_error" while the ion chambers are filled), so while a subsystem is
    busy the error is the last word and the state is everything before it.
    When it is idle, everything after IDLE is the error.
    """
    if not response.ok:
        raise ProtocolError(f"status request failed: {response.error}")
    words = response.first.split()
    if words[0].upper() == IDLE:
        state, error = IDLE, " ".join(words[1:])
    elif len(words) > 1:
        state, error = " ".join(words[:-1]), words[-1]
    else:
        state, error = words[0], ""
    if error == "" or error.upper() == NO_ERROR:
        error = NO_ERROR
    return SubsystemStatus(state=state, error=error)
