"""One TCP connection to the control server.

The protocol is strictly request/response, so a lock makes sure only one
request is in flight per connection. The connection is opened on first use and
reopened after any failure.
"""

import asyncio
import logging

from .protocol import (
    RESPONSE_TERMINATOR,
    Response,
    encode_request,
    parse_response,
)

log = logging.getLogger(__name__)


class ControlConnectionError(Exception):
    """The control server could not be reached or stopped answering."""


class ControlClient:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout_s: float = 5.0,
        response_timeout_s: float = 10.0,
        name: str = "control",
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout_s = connect_timeout_s
        self._response_timeout_s = response_timeout_s
        self._name = name
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        async with self._lock:
            await self._ensure_connected()

    async def close(self) -> None:
        async with self._lock:
            await self._drop()

    async def request(self, command: str) -> Response:
        """Send one command and return the parsed response.

        Raises ControlConnectionError if the server cannot be reached or does
        not answer in time. A response starting with ERROR is not an exception;
        check Response.ok.
        """
        payload = encode_request(command)
        async with self._lock:
            await self._ensure_connected()
            assert self._reader is not None and self._writer is not None
            try:
                self._writer.write(payload)
                await self._writer.drain()
                raw = await asyncio.wait_for(self._read_response(), self._response_timeout_s)
            except (
                OSError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
                TimeoutError,
            ) as exc:
                # After a failure the stream position is unknown, so reconnect.
                await self._drop()
                raise ControlConnectionError(
                    f"{self._name}: no valid answer to {command!r}: {exc!r}"
                ) from exc
            return parse_response(raw)

    async def raw_exchange(self, command: str, listen_s: float = 1.0) -> bytes:
        """Send a command and return every byte received within listen_s.

        Diagnostic only (used by tools/capture_raw.py); makes no assumption
        about framing.
        """
        payload = encode_request(command)
        async with self._lock:
            await self._ensure_connected()
            assert self._reader is not None and self._writer is not None
            self._writer.write(payload)
            await self._writer.drain()
            buffer = bytearray()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + listen_s
            while (remaining := deadline - loop.time()) > 0:
                try:
                    chunk = await asyncio.wait_for(self._reader.read(8192), remaining)
                except TimeoutError:
                    break
                if not chunk:
                    break
                buffer += chunk
            return bytes(buffer)

    async def _read_response(self) -> bytes:
        assert self._reader is not None
        return await self._reader.readuntil(RESPONSE_TERMINATOR)

    async def _ensure_connected(self) -> None:
        if self.connected:
            return
        await self._drop()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._connect_timeout_s
            )
        except (OSError, TimeoutError) as exc:
            raise ControlConnectionError(
                f"{self._name}: cannot connect to {self._host}:{self._port}: {exc!r}"
            ) from exc
        log.info("%s: connected to %s:%s", self._name, self._host, self._port)

    async def _drop(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
