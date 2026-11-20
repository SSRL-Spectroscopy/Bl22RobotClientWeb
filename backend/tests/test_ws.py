import time

from fastapi.testclient import TestClient


def test_websocket_sends_snapshot_then_updates():
    from robotweb.main import app

    with TestClient(app) as http:
        store = app.state.store
        now = time.time()
        store.publish("pv.TEST", {"connected": True, "value": 1.5, "ts": now})
        store.add_sample("pv.TEST", now, 1.5)

        with http.websocket_connect("/api/ws") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["window_s"] == 300
            assert snapshot["state"]["pv.TEST"]["value"] == 1.5
            assert snapshot["history"]["pv.TEST"]["v"] == [1.5]
            # not polled: unknown, or the first request has already failed
            assert snapshot["state"]["control"]["connected"] in (None, False)

            store.publish("pv.TEST", {"connected": True, "value": 2.5, "ts": now + 1})
            store.add_sample("pv.TEST", now + 1, 2.5)
            while True:
                update = ws.receive_json()
                assert update["type"] == "update"
                if "pv.TEST" in update["changes"]:
                    break
            assert update["changes"]["pv.TEST"]["value"] == 2.5
            # A sample taken just before the snapshot can appear again in the
            # first update; the browser drops points it already has.
            assert update["samples"]["pv.TEST"]["v"][-1] == 2.5
