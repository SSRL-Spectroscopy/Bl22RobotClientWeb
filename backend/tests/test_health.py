from fastapi.testclient import TestClient


def test_health_reports_unreachable_control_server():
    from robotweb.main import app

    with TestClient(app) as http:
        body = http.get("/api/health").json()
    assert body["backend"] == "ok"
    assert body["control_server"]["connected"] is False


def test_pv_list():
    from robotweb.main import app

    with TestClient(app) as http:
        pvs = http.get("/api/pvs").json()
    plotted = [pv["label"] for pv in pvs if pv["plot"]]
    assert plotted == ["I_0", "I_1", "I_2"]
    assert all(pv["key"] == f"pv.{pv['name']}" for pv in pvs)
