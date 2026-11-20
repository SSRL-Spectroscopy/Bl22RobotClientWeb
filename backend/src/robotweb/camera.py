"""The IP camera (AXIS, MJPEG over HTTP), passed on to the pages.

- One connection to the camera, however many pages are open. It is opened
  when the first page asks for the picture and closed a few seconds after the
  last one has left.
- Latency before picture quality: a page that cannot keep up gets the newest
  picture, the ones in between are dropped. Nothing is buffered.
- The camera's user name and password stay in the backend (.env). They are
  sent in the HTTP authentication header (digest), never in the address, and
  are never logged or published.

The picture is read from the camera's VAPIX address
`/axis-cgi/mjpg/video.cgi`.

An operator can turn and zoom the camera (PTZ, `/axis-cgi/com/ptz.cgi`):

- Turning and zooming go on only while the page keeps asking for them. If
  the page stops asking (button released, page closed, network gone) the
  backend stops the camera after PTZ_HOLD_S by itself.
- The camera account in the settings needs the right to do this (an
  "operator" account of the camera); a viewer account only shows the picture.
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator

import httpx

from .state import StateStore

log = logging.getLogger(__name__)

KEY = "camera"
BOUNDARY = "frame"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"

_HEADER_END = b"\r\n\r\n"
_LENGTH = re.compile(rb"content-length:\s*(\d+)", re.IGNORECASE)
_RESOLUTION = re.compile(r"^\d{2,5}x\d{2,5}$")
_PRESET = re.compile(r"^presetposno(\d+)=(.+)$", re.MULTILINE)

# A move goes on this long after the page last asked for it.
PTZ_HOLD_S = 1.2
_MAX_HEADER = 16 * 1024
_MAX_FRAME = 20 * 1024 * 1024


class CameraError(Exception):
    """The camera sent something that is not the expected stream."""


def take_frames(buffer: bytearray) -> list[bytes]:
    """Cut the complete pictures off the front of `buffer` (the multipart
    stream as received so far) and return them. What is left in the buffer
    is the beginning of the next part."""
    frames: list[bytes] = []
    while True:
        end = buffer.find(_HEADER_END)
        if end < 0:
            if len(buffer) > _MAX_HEADER:
                raise CameraError("the camera does not send a multipart picture stream")
            return frames
        found = _LENGTH.search(bytes(buffer[:end]))
        if found is None:
            raise CameraError("a picture of the camera came without its length")
        length = int(found.group(1))
        if length > _MAX_FRAME:
            raise CameraError(f"the camera announced a picture of {length} bytes")
        start = end + len(_HEADER_END)
        if len(buffer) < start + length:
            return frames
        frames.append(bytes(buffer[start : start + length]))
        del buffer[: start + length]


def parse_presets(text: str) -> list[str]:
    """The names of the camera's preset positions, in the camera's order,
    from its answer to "query=presetposall"."""
    found = sorted((int(number), name.strip()) for number, name in _PRESET.findall(text.replace("\r", "")))
    return [name for _, name in found if name]


def _speed(value: float) -> int:
    return max(-100, min(100, round(value)))


def part(frame: bytes) -> bytes:
    """One picture as a part of the stream sent to a page."""
    head = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
    return head.encode("ascii") + frame + b"\r\n"


def address(host: str) -> tuple[str, str]:
    """(scheme, host[:port]) from what was entered as the camera's host. An
    address pasted from a browser works too: "http://" or "https://" in front
    and a path behind are accepted, and a user name in it is dropped."""
    host = host.strip()
    scheme = "http"
    if "://" in host:
        scheme, host = host.split("://", 1)
        scheme = scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"camera host: {scheme}:// is not supported, only http:// and https://")
    host = host.split("/", 1)[0].rsplit("@", 1)[-1]
    return scheme, host


class Camera:
    def __init__(
        self,
        store: StateStore,
        *,
        host: str = "",
        user: str = "",
        password: str = "",
        resolution: str = "1280x720",
        fps: int = 15,
        compression: int = 30,
        linger_s: float = 5.0,
        retry_s: float = 3.0,
        read_timeout_s: float = 5.0,
    ) -> None:
        """`host`: address of the camera, with a port if it is not 80, and
        with "https://" in front if it only answers there. Empty: there is no
        camera. `linger_s`: how long the connection to the camera
        is kept after the last page has left."""
        if host and not _RESOLUTION.match(resolution):
            raise ValueError(f"camera resolution {resolution!r} is not of the form 1280x720")
        self._store = store
        self._scheme, self._host = address(host)
        self._auth = httpx.DigestAuth(user, password) if user else None
        self._query = {"resolution": resolution, "fps": str(int(fps)), "compression": str(int(compression))}
        self._linger_s = linger_s
        self._retry_s = retry_s
        self._read_timeout_s = read_timeout_s

        self._viewers = 0
        self._last_left = 0.0
        self._frame: bytes | None = None
        self._count = 0  # goes up with every picture
        self._news = asyncio.Condition()
        self._task: asyncio.Task | None = None
        self._closing = False
        self._state: dict | None = None

        # PTZ. presets: None until the camera has been asked.
        width, height = resolution.split("x") if self._host else ("0", "0")
        self._size = (int(width), int(height))
        self._control: httpx.AsyncClient | None = None
        self._ptz_lock = asyncio.Lock()
        self._moving = (0, 0)  # pan and tilt speed the camera was last told
        self._zooming = 0
        self._hold_until = 0.0
        self._guard: asyncio.Task | None = None
        self.presets: list[str] | None = None
        self.ptz_detail: str | None = None
        self._publish(False, None)

    @property
    def configured(self) -> bool:
        return bool(self._host)

    @property
    def url(self) -> str:
        return f"{self._scheme}://{self._host}/axis-cgi/mjpg/video.cgi"

    # ---- for the pages ------------------------------------------------------

    async def frames(self) -> AsyncIterator[bytes]:
        """The pictures, newest first: one that came while the caller was
        busy with the one before replaces it. Ends when the backend stops."""
        self._viewers += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="camera")
        seen = self._count
        try:
            while not self._closing:
                async with self._news:
                    try:
                        await asyncio.wait_for(
                            self._news.wait_for(lambda: self._count != seen or self._closing), timeout=1.0
                        )
                    except TimeoutError:
                        continue  # look at _closing again
                    frame, seen = self._frame, self._count
                if frame is not None and not self._closing:
                    yield frame
        finally:
            self._viewers -= 1
            self._last_left = time.monotonic()

    def end_streams(self) -> None:
        """Let every page's stream end (the backend is stopping). Safe to
        call more than once."""
        self._closing = True

    async def close(self) -> None:
        self.end_streams()
        if self._guard is not None and not self._guard.done():
            self._guard.cancel()
        if self._moving != (0, 0) or self._zooming:
            try:
                await self.stop()  # do not leave the camera turning
            except CameraError as exc:
                log.warning("camera: could not stop it while closing: %s", exc)
        if self._control is not None:
            await self._control.aclose()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---- the connection to the camera ------------------------------------------

    def _wanted(self) -> bool:
        if self._closing:
            return False
        return self._viewers > 0 or time.monotonic() - self._last_left < self._linger_s

    async def _run(self) -> None:
        timeout = httpx.Timeout(self._read_timeout_s, read=self._read_timeout_s)
        await self.read_presets()
        try:
            # verify=False: with https a camera on the local network shows a
            # certificate it has signed itself, which cannot be checked.
            async with httpx.AsyncClient(auth=self._auth, timeout=timeout, verify=False) as http:
                while self._wanted():
                    try:
                        await self._read(http)
                    except CameraError as exc:
                        self._lost(str(exc))
                    except httpx.TimeoutException:
                        self._lost("the camera does not answer")
                    except httpx.HTTPError as exc:
                        # The text of these errors holds no credentials: they
                        # are never part of the address.
                        reason = str(exc).strip() or type(exc).__name__
                        self._lost(f"no connection to the camera at {self._scheme}://{self._host} ({reason})")
                    else:
                        continue  # ended on purpose or by the camera: look again
                    await self._pause()
        finally:
            self._frame = None
            self._publish(False, None)

    async def _read(self, http: httpx.AsyncClient) -> None:
        async with http.stream("GET", self.url, params=self._query) as response:
            if response.status_code == 401:
                raise CameraError("the camera refused the user name or the password")
            if response.status_code != 200:
                raise CameraError(f"the camera answered with HTTP {response.status_code}")
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer += chunk
                frames = take_frames(buffer)
                if frames:
                    await self._show(frames[-1])
                if not self._wanted():
                    return
        raise CameraError("the camera ended the picture stream")

    async def _show(self, frame: bytes) -> None:
        self._publish(True, None)
        async with self._news:
            self._frame = frame
            self._count += 1
            self._news.notify_all()

    def _lost(self, detail: str) -> None:
        if self._state is None or self._state["detail"] != detail:
            log.warning("camera: %s", detail)
        self._publish(False, detail)

    async def _pause(self) -> None:
        end = time.monotonic() + self._retry_s
        while self._wanted() and time.monotonic() < end:
            await asyncio.sleep(0.1)

    # ---- turning and zooming (PTZ) -----------------------------------------------

    async def _ptz(self, **params: str) -> str:
        if not self.configured:
            raise CameraError("No camera is configured.")
        if self._control is None:
            self._control = httpx.AsyncClient(auth=self._auth, timeout=3.0, verify=False)
        url = f"{self._scheme}://{self._host}/axis-cgi/com/ptz.cgi"
        try:
            # A limit for the whole answer: the per-read timeout alone would
            # let an answer that never ends go on for ever.
            response = await asyncio.wait_for(self._control.get(url, params={"camera": "1", **params}), 5.0)
        except TimeoutError as exc:
            raise CameraError("the camera did not answer the command in time") from exc
        except httpx.HTTPError as exc:
            raise CameraError(f"the camera did not take the command ({str(exc).strip() or type(exc).__name__})") from exc
        if response.status_code in (401, 403):
            raise CameraError("the camera account in the backend's settings is not allowed to do this")
        if response.status_code not in (200, 204):
            raise CameraError(f"the camera answered with HTTP {response.status_code}")
        text = response.text.strip()
        if text.lower().startswith("error"):
            raise CameraError(f"the camera answered: {text[:200]}")
        return text

    async def read_presets(self) -> None:
        """Ask the camera for its preset positions. Never raises: without an
        answer PTZ is shown as not available, with the reason."""
        try:
            self.presets = parse_presets(await self._ptz(query="presetposall"))
            self.ptz_detail = None
        except CameraError as exc:
            self.presets = None
            self.ptz_detail = str(exc)
        if self._state is not None:
            self._publish(self._state["connected"], self._state["detail"])

    async def move(self, pan: float, tilt: float) -> None:
        """Turn at these speeds (-100 to 100; right and up are positive) until
        told otherwise, or until nobody has asked again for PTZ_HOLD_S."""
        vector = (_speed(pan), _speed(tilt))
        async with self._ptz_lock:
            self._hold_until = time.monotonic() + PTZ_HOLD_S
            if vector != self._moving:
                await self._ptz(continuouspantiltmove=f"{vector[0]},{vector[1]}")
                self._moving = vector
            self._watch()

    async def zoom(self, speed: float) -> None:
        """Zoom at this speed (-100 to 100; in is positive), like move()."""
        speed = _speed(speed)
        async with self._ptz_lock:
            self._hold_until = time.monotonic() + PTZ_HOLD_S
            if speed != self._zooming:
                await self._ptz(continuouszoommove=str(speed))
                self._zooming = speed
            self._watch()

    async def stop(self) -> None:
        """Stop turning and zooming. Both are always sent."""
        async with self._ptz_lock:
            await self._ptz(continuouspantiltmove="0,0")
            self._moving = (0, 0)
            await self._ptz(continuouszoommove="0")
            self._zooming = 0

    async def home(self) -> None:
        await self._ptz(move="home")

    async def goto(self, name: str) -> None:
        if not self.presets or name not in self.presets:
            raise CameraError(f"the camera has no preset position called {name!r}")
        await self._ptz(gotoserverpresetname=name)

    async def center(self, x: float, y: float) -> None:
        """Turn so that this point of the picture comes to its middle. x and
        y: 0 to 1 from the left and from the top."""
        width, height = self._size
        await self._ptz(
            center=f"{round(x * width)},{round(y * height)}", imagewidth=str(width), imageheight=str(height)
        )

    def _watch(self) -> None:
        if self._guard is None or self._guard.done():
            self._guard = asyncio.create_task(self._stop_when_left(), name="camera-ptz-guard")

    async def _stop_when_left(self) -> None:
        """Stops the camera when the page has not asked again in time."""
        while self._moving != (0, 0) or self._zooming:
            if time.monotonic() >= self._hold_until:
                try:
                    await self.stop()
                except CameraError as exc:
                    log.warning("camera: could not stop it, trying again: %s", exc)
                    await asyncio.sleep(0.5)
            await asyncio.sleep(0.05)

    def _publish(self, connected: bool, detail: str | None) -> None:
        state = {
            "configured": self.configured,
            "connected": connected,
            "detail": detail,
            # None: the camera has not been asked, or did not answer (ptz_detail)
            "presets": self.presets,
            "ptz_detail": self.ptz_detail,
        }
        if state != self._state:
            self._state = state
            self._store.publish(KEY, {**state, "ts": time.time()})
