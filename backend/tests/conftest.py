import pytest


@pytest.fixture(autouse=True)
def _no_live_systems(monkeypatch, tmp_path):
    """Tests never touch EPICS, a real control server, or the real accounts.

    Port 1 refuses connections at once; tests that need a control server start
    a FakeControlServer and point the settings at it.
    """
    monkeypatch.setenv("ROBOTWEB_ENV_FILE", str(tmp_path / "no-such.env"))
    monkeypatch.setenv("ROBOTWEB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ROBOTWEB_EPICS_ENABLED", "0")
    monkeypatch.setenv("ROBOTWEB_CONTROL_HOST", "127.0.0.1")
    monkeypatch.setenv("ROBOTWEB_CONTROL_PORT", "1")
    monkeypatch.setenv("ROBOTWEB_CONTROL_CONNECT_TIMEOUT_S", "1")
