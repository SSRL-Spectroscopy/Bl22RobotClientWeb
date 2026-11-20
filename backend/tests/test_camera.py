"""The camera stream, against a small stand-in for the camera."""

import asyncio
import contextlib
import time

import pytest
from fastapi.testclient import TestClient

from robotweb.camera import Camera, CameraError, address, part, take_frames
from robotweb.state import StateStore


def jpeg(n: int) -> bytes:
    return b"\xff\xd8" + bytes([n]) * (50 + n) + b"\xff\xd9"


def axis_part(frame: bytes) -> bytes:
    return b"--myboundary\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n" % (len(frame), frame)


class FakeCamera:
    """Answers every request with pictures 0, 1, 2, ... every `every_s`."""

    def __init__(self, *, status: int = 200, every_s: float = 0.02) -> None:
        self.status = status
        self.every_s = every_s
        self.requests: list[str] = []
        self.open = 0
        self._writers: set[asyncio.StreamWriter] = set()

    async def __aenter__(self) -> "FakeCamera":
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.host = "127.0.0.1:%d" % self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        self.drop()

    def drop(self) -> None:
        for writer in list(self._writers):
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        self.open += 1
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            self.requests.append(head.splitlines()[0])
            if self.status != 200:
                writer.write(b"HTTP/1.1 %d Nope\r\nContent-Length: 0\r\nConnection: close\r\n\r\n" % self.status)
                await writer.drain()
                return
            writer.write(
                b"HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=myboundary\r\n\r\n"
            )
            n = 0
            while True:
                writer.write(axis_part(jpeg(n % 200)))
                await writer.drain()
                n += 1
                await asyncio.sleep(self.every_s)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.open -= 1
            self._writers.discard(writer)
            writer.close()


def make(host: str, store: StateStore | None = None, **more) -> Camera:
    options = {"linger_s": 0.1, "retry_s": 0.05, "read_timeout_s": 1.0, **more}
    return Camera(store or StateStore(), host=host, **options)


async def until(condition, what: str = "condition") -> None:
    end = time.monotonic() + 5.0  # by the clock: short sleeps can return at once
    while time.monotonic() < end:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{what} did not happen")


async def take(camera: Camera, n: int) -> list[bytes]:
    got = []
    # closed at once, as the web server does when a page leaves
    async with contextlib.aclosing(camera.frames()) as stream:
        async for frame in stream:
            got.append(frame)
            if len(got) == n:
                break
    return got


# ---- cutting the stream into pictures -----------------------------------------


def test_pictures_are_cut_out_of_the_stream_however_it_arrives():
    stream = b"".join(axis_part(jpeg(n)) for n in range(3))
    for size in (1, 7, 64, len(stream)):
        buffer, frames = bytearray(), []
        for at in range(0, len(stream), size):
            buffer += stream[at : at + size]
            frames += take_frames(buffer)
        assert frames == [jpeg(0), jpeg(1), jpeg(2)], size


def test_a_stream_that_is_not_pictures_is_refused():
    with pytest.raises(CameraError, match="length"):
        take_frames(bytearray(b"--b\r\nContent-Type: image/jpeg\r\n\r\nxxxx"))
    with pytest.raises(CameraError, match="multipart"):
        take_frames(bytearray(b"<html>" + b"x" * 20000))


def test_a_part_for_the_page_carries_the_picture():
    assert take_frames(bytearray(part(jpeg(5)))) == [jpeg(5)]


# ---- the connection to the camera -----------------------------------------------


async def test_pictures_are_passed_on_and_the_request_carries_the_settings():
    async with FakeCamera() as fake:
        store = StateStore()
        camera = make(fake.host, store, resolution="800x450", fps=10, compression=40)
        assert store.get("camera")["configured"] is True and store.get("camera")["connected"] is False
        frames = await take(camera, 3)
        assert all(f.startswith(b"\xff\xd8") and f.endswith(b"\xff\xd9") for f in frames)
        assert store.get("camera")["connected"] is True
        assert fake.requests == ["GET /axis-cgi/mjpg/video.cgi?resolution=800x450&fps=10&compression=40 HTTP/1.1"]
        await camera.close()


async def test_many_pages_share_one_connection_which_is_closed_when_they_have_left():
    async with FakeCamera() as fake:
        store = StateStore()
        camera = make(fake.host, store)
        a, b, c = await asyncio.gather(take(camera, 3), take(camera, 3), take(camera, 3))
        assert len(a) == len(b) == len(c) == 3
        assert len(fake.requests) == 1
        await until(lambda: fake.open == 0, "closing the connection to the camera")
        assert store.get("camera")["connected"] is False and store.get("camera")["detail"] is None
        # the next page opens it again
        await take(camera, 1)
        assert len(fake.requests) == 2
        await camera.close()


async def test_a_slow_page_gets_the_newest_picture_not_a_backlog():
    async with FakeCamera() as fake:
        camera = make(fake.host)
        got = []
        async with contextlib.aclosing(camera.frames()) as stream:
            async for frame in stream:
                got.append(frame[2])
                await asyncio.sleep(0.3)  # many pictures come meanwhile
                if len(got) == 3:
                    break
        # a backlog would be the very next picture each time
        assert (got[1] - got[0]) % 200 > 3 and (got[2] - got[1]) % 200 > 3
        await camera.close()


async def test_the_connection_is_made_again_after_the_camera_dropped_it():
    async with FakeCamera() as fake:
        store = StateStore()
        camera = make(fake.host, store)
        stream = camera.frames()
        await anext(stream)
        fake.drop()
        await until(lambda: store.get("camera")["detail"] is not None, "noticing the loss")
        await until(lambda: len(fake.requests) == 2 and store.get("camera")["connected"], "reconnecting")
        await anext(stream)
        await stream.aclose()
        await camera.close()


async def test_a_refused_password_is_said_without_showing_it():
    async with FakeCamera(status=401) as fake:
        store = StateStore()
        camera = make(fake.host, store, user="viewer", password="s3cret-pw")
        stream = camera.frames()
        waiting = asyncio.ensure_future(anext(stream))
        await until(lambda: store.get("camera")["detail"] is not None, "the refusal")
        state = store.get("camera")
        assert state["connected"] is False and "refused the user name or the password" in state["detail"]
        assert "s3cret-pw" not in repr(state) and "viewer" not in repr(state)
        assert "s3cret-pw" not in camera.url and "viewer" not in camera.url
        camera.end_streams()
        with pytest.raises(StopAsyncIteration):
            await waiting
        await camera.close()


async def test_a_camera_that_is_away_is_reported():
    store = StateStore()
    camera = make("127.0.0.1:1", store)
    stream = camera.frames()
    waiting = asyncio.ensure_future(anext(stream))
    await until(lambda: store.get("camera")["detail"] is not None, "the report")
    # refused at once, or (on Windows) no answer within the timeout
    assert store.get("camera")["detail"].startswith(("no connection to the camera", "the camera does not answer"))
    await camera.close()
    with pytest.raises(StopAsyncIteration):
        await waiting


async def test_streams_end_when_the_backend_stops():
    async with FakeCamera() as fake:
        camera = make(fake.host)
        stream = camera.frames()
        await anext(stream)
        camera.end_streams()
        with pytest.raises(StopAsyncIteration):
            while True:
                await anext(stream)
        await camera.close()
        await until(lambda: fake.open == 0)


def test_the_host_may_be_an_address_pasted_from_a_browser():
    assert address(" 10.0.0.5 ") == ("http", "10.0.0.5")
    assert address("10.0.0.5:8080") == ("http", "10.0.0.5:8080")
    assert address("http://10.0.0.5/") == ("http", "10.0.0.5")
    assert address("HTTPS://cam.example/camera/index.html") == ("https", "cam.example")
    assert address("http://viewer:pw@10.0.0.5/") == ("http", "10.0.0.5")  # credentials never in the address
    assert Camera(StateStore(), host="https://viewer:pw@cam.example").url == "https://cam.example/axis-cgi/mjpg/video.cgi"
    with pytest.raises(ValueError, match="rtsp"):
        address("rtsp://10.0.0.5")


def test_a_wrong_resolution_stops_the_start():
    with pytest.raises(ValueError, match="1280x720"):
        Camera(StateStore(), host="camera", resolution="720p&x=1")


# ---- the endpoint ------------------------------------------------------------


def test_without_a_camera_there_is_no_stream_and_the_page_is_told():
    from robotweb.main import app

    with TestClient(app) as http:
        assert http.get("/api/camera/stream").status_code == 404
        assert app.state.store.get("camera")["configured"] is False
