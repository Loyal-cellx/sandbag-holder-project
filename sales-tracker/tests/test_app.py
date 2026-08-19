"""Route-level tests for the Flask app."""
import json

import database


def _add(db, **kw):
    defaults = dict(date_str="2026-06-01", amount=69.0, location="Texas",
                    platform="Amazon", notes="")
    defaults.update(kw)
    db.add_sale(**defaults)
    return db.get_all_sales()[0]["id"]


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_log_bad_date_flashes_and_redirects(client):
    resp = client.post("/log", data={
        "date": "2026-13-45", "amount": "10", "location": "Texas", "platform": "Amazon",
    })
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("_flashes")  # a flash message was queued
    # The bad row must not have been written.
    assert database.get_stats()["total_sales"] == 0


def test_log_good_date_creates_row(client):
    resp = client.post("/log", data={
        "date": "2026-06-01", "amount": "69", "location": "Texas", "platform": "Amazon",
    })
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")
    assert database.get_stats()["total_sales"] == 1


def test_edit_nonexistent_id_404(client):
    resp = client.post("/sales/9999/edit", data=json.dumps({"amount": 10}),
                       content_type="application/json")
    assert resp.status_code == 404


def test_edit_without_json_400(client):
    sale_id = _add(database)
    resp = client.post(f"/sales/{sale_id}/edit", data="not json",
                       content_type="text/plain")
    assert resp.status_code == 400


def test_edit_returns_server_profit(client):
    sale_id = _add(database, amount=69.0)
    resp = client.post(f"/sales/{sale_id}/edit", data=json.dumps({"amount": 138.0}),
                       content_type="application/json")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["profit"] == round(138.0 * database.PROFIT_MARGIN, 2)


def test_delete_requires_fetch_header_403(client):
    sale_id = _add(database)
    resp = client.post(f"/sales/{sale_id}/delete")  # no X-Requested-With
    assert resp.status_code == 403
    assert database.get_stats()["total_sales"] == 1  # not deleted


def test_delete_with_header_then_404(client):
    sale_id = _add(database)
    ok = client.post(f"/sales/{sale_id}/delete", headers={"X-Requested-With": "fetch"})
    assert ok.status_code == 200
    gone = client.post(f"/sales/{sale_id}/delete", headers={"X-Requested-With": "fetch"})
    assert gone.status_code == 404


def test_api_stats(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    assert "total_sales" in resp.get_json()


def test_api_prediction_keys(client, monkeypatch):
    # Keep the route offline + fast: stub the external feeds so no network I/O.
    import prediction
    monkeypatch.setattr(prediction, "_get_cached_alerts", lambda codes: [])
    monkeypatch.setattr(prediction, "_cached", lambda *a, **k: [])
    resp = client.get("/api/prediction")
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ("score", "label", "color", "time_pts", "weather_pts",
                "season_pts", "combo_pts", "nhc_pts", "eta_days",
                "alerts", "storms", "generated_at", "cache_ttl_seconds"):
        assert key in body, f"missing {key}"
    assert body["cache_ttl_seconds"] == 1800


def test_index_never_calls_get_prediction(client, monkeypatch):
    # The dashboard must render instantly; the forecast arrives via /partial/forecast.
    import app as app_module

    def boom(*a, **kw):
        raise AssertionError("index() must not call get_prediction")

    monkeypatch.setattr(app_module, "get_prediction", boom)
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"forecastSlot" in resp.data


def test_partial_forecast_offline_empty_db(client, monkeypatch):
    # No sales -> no avg_freq -> empty fragment, but never a 500.
    import prediction
    monkeypatch.setattr(prediction, "_get_cached_alerts", lambda codes: [])
    monkeypatch.setattr(prediction, "_cached", lambda *a, **kw: [])
    resp = client.get("/partial/forecast")
    assert resp.status_code == 200


def test_partial_forecast_renders_score(client, monkeypatch):
    import prediction
    monkeypatch.setattr(prediction, "_get_cached_alerts", lambda codes: [])
    monkeypatch.setattr(prediction, "_cached", lambda *a, **kw: [])
    _add(database, date_str="2026-06-01")
    _add(database, date_str="2026-06-08")
    resp = client.get("/partial/forecast")
    assert resp.status_code == 200
    assert b"predScore" in resp.data
    assert b'id="predFill"' in resp.data
    assert b"data-score=" in resp.data
