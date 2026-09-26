import logging
import re

from app.main import STATIC_DIR


def test_dashboard_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<title>Sensor Fleet</title>" in resp.text
    assert "x-request-id" in resp.headers


def test_static_assets_served(client):
    js = client.get("/static/app.js")
    css = client.get("/static/styles.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert client.get("/static/nope.js").status_code == 404


def test_static_requests_logged_with_bounded_path(client, caplog):
    caplog.set_level(logging.INFO)
    client.get("/static/app.js")
    client.get("/")
    paths = [r.path for r in caplog.records if getattr(r, "event", None) == "http_request"]
    assert paths[-2:] == ["/static", "/"]


def test_dashboard_not_in_openapi_schema(client):
    assert "/" not in client.get("/openapi.json").json()["paths"]


def test_ui_never_uses_inner_html_and_calls_only_public_routes():
    js = (STATIC_DIR / "app.js").read_text()
    assert ".innerHTML" not in js and "insertAdjacentHTML" not in js
    called = set(re.findall(r'api\(\s*[`"](/[a-z/]+)', js))
    assert called <= {"/health", "/devices", "/anomalies", "/admin/faults", "/readings", "/devices/"}
