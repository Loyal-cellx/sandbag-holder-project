from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from database import db_init, add_sale, get_all_sales, get_stats, get_distinct_locations, get_all_locations, delete_sale, update_sale, get_milestones, get_sale, get_climate_snapshots, save_climate_snapshot, save_sale_weather, get_sale_weather, get_all_sale_weather, get_sales_missing_weather
from prediction import get_prediction
from datetime import date, datetime, timezone
import os
import json
import threading
import urllib.request
import urllib.error
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-env")

# Idempotent table creation — runs at import time so wsgi servers (gunicorn/etc.)
# also have a ready table on first request, not just `python app.py`.
db_init()

VALID_PLATFORMS = {"Amazon", "eBay", "Walmart"}

NETWORTH_REVENUE_URL = os.getenv("NETWORTH_API_URL", "http://dataworks:5052/api/revenue")


def _fetch_weather_for_sale_background(sale_id: int, date_str: str, location: str):
    """Fire-and-forget: fetch historical weather for a sale and save to DB."""
    import sys
    import json
    import urllib.parse
    sys.path.insert(0, os.path.dirname(__file__))
    from backfill_weather import STATE_COORDS, WMO_DESC, fetch_weather

    def _do():
        try:
            loc = location.strip().title()
            coords = STATE_COORDS.get(loc)
            if not coords:
                return
            lat, lon = coords
            weather = fetch_weather(lat, lon, date_str)
            if not weather:
                return
            weather["sale_date"] = date_str
            weather["location"] = loc
            weather["fetched_at"] = datetime.now(timezone.utc).isoformat()
            save_sale_weather(sale_id, weather)
        except Exception:
            pass

    threading.Thread(target=_do, daemon=True).start()


def _sync_revenue_background():
    """Push all monthly revenue totals to the net worth tracker (upsert, fire-and-forget)."""
    def _do_sync():
        try:
            by_month = get_stats().get("by_month", [])
            for entry in by_month:
                payload = json.dumps({
                    "month": entry["month"],
                    "amount": round(entry["revenue"], 2),
                    "units": entry["count"],
                    "note": "sandbag dashboard sync",
                }).encode()
                req = urllib.request.Request(
                    NETWORTH_REVENUE_URL,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
        except Exception:
            pass  # Never let a sync failure affect the sale-logging flow

    threading.Thread(target=_do_sync, daemon=True).start()


@app.route("/")
def index():
    sales = get_all_sales()
    stats = get_stats()
    prediction = get_prediction(stats, get_all_locations())
    return render_template("index.html", sales=sales, stats=stats, prediction=prediction, year=datetime.now().year)


@app.route("/log", methods=["GET", "POST"])
def log_sale():
    if request.method == "POST":
        sale_date = request.form.get("date", "").strip()
        raw_amount = request.form.get("amount", "").strip()
        location = request.form.get("location", "").strip().title()
        platform = request.form.get("platform", "").strip()
        notes = request.form.get("notes", "").strip()

        # Validate
        try:
            amount = float(raw_amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash("Amount must be a positive number.")
            return redirect(url_for("log_sale"))

        if not location:
            flash("Location is required.")
            return redirect(url_for("log_sale"))

        if platform not in VALID_PLATFORMS:
            flash("Platform must be Amazon, eBay, or Walmart.")
            return redirect(url_for("log_sale"))

        try:
            datetime.strptime(sale_date, "%Y-%m-%d")
        except ValueError:
            flash("Date must be a valid calendar date (YYYY-MM-DD).")
            return redirect(url_for("log_sale"))

        sale_id = add_sale(sale_date, amount, location, platform, notes)
        _sync_revenue_background()
        _fetch_weather_for_sale_background(sale_id, sale_date, location)
        return redirect(url_for("index"))

    today = date.today().isoformat()
    locations = get_distinct_locations()
    return render_template("log_sale.html", today=today, locations=locations)


@app.route("/sales/<int:sale_id>/delete", methods=["POST"])
def delete_sale_route(sale_id):
    # Require the custom fetch header — browsers won't attach it on a cross-site
    # form/navigation, so this rejects naive CSRF without a token scheme.
    if request.headers.get("X-Requested-With") != "fetch":
        return jsonify({"ok": False, "error": "Forbidden"}), 403
    if not delete_sale(sale_id):
        return jsonify({"ok": False, "error": "Sale not found"}), 404
    _sync_revenue_background()
    return jsonify({"ok": True})


@app.route("/sales/<int:sale_id>/edit", methods=["POST"])
def edit_sale_route(sale_id):
    # silent=True + JSON content-type requirement: a non-JSON body yields None
    # (→ 400), and the application/json type forces a CORS preflight cross-site.
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "Expected JSON body"}), 400
    amount = data.get("amount")
    notes = data.get("notes")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Amount must be a number"}), 400
        if amount <= 0:
            return jsonify({"ok": False, "error": "Amount must be positive"}), 400
    if not update_sale(sale_id, amount=amount, notes=notes):
        return jsonify({"ok": False, "error": "Sale not found"}), 404
    _sync_revenue_background()
    sale = get_sale(sale_id)
    return jsonify({"ok": True, "amount": sale["amount"], "profit": sale["profit"]})


@app.route("/milestones")
def milestones():
    data = get_milestones()
    return render_template("milestones.html", data=data)


@app.route("/api/sales")
def api_sales():
    return jsonify(get_all_sales())


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


@app.route("/api/prediction")
def api_prediction():
    prediction = get_prediction(get_stats(), get_all_locations())
    prediction["generated_at"] = datetime.now(timezone.utc).isoformat()
    prediction["cache_ttl_seconds"] = 1800
    return jsonify(prediction)


@app.route("/api/sale-weather")
def api_sale_weather():
    return jsonify(get_all_sale_weather())


@app.route("/api/sale-weather/<int:sale_id>")
def api_sale_weather_one(sale_id):
    data = get_sale_weather(sale_id)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/history")
def history():
    snapshots = get_climate_snapshots(limit=365)
    sale_weather = get_all_sale_weather()
    return render_template("history.html", snapshots=snapshots, sale_weather=sale_weather)


@app.route("/api/snapshots")
def api_snapshots():
    return jsonify(get_climate_snapshots(limit=365))


@app.route("/api/take-snapshot", methods=["POST"])
def api_take_snapshot():
    """Trigger a climate+sales snapshot for today. Called by nightly cron."""
    try:
        from snapshot import take_snapshot
        snap = take_snapshot()
        return jsonify({"ok": True, "snapshot_date": snap["snapshot_date"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
