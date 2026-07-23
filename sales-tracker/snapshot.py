"""
snapshot.py — Daily climate + sales snapshot collector.

Callable as:
  python snapshot.py               # take today's snapshot
  python snapshot.py --date 2026-06-22   # back-fill a specific date

Idempotent: running twice on the same date overwrites with fresh data.
"""

import sys
import json
import math
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime, timezone, timedelta
import os

# ── Path setup so we can import database ────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from database import save_climate_snapshot, get_stats, db_init

# ── NOAA / NIFC constants (mirrors prediction.py) ────────────────────────────
NOAA_ALERTS_URL = "https://api.weather.gov/alerts/active?status=actual&message_type=alert"

NIFC_ACTIVE_URL = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/"
    "services/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
)
MIN_FIRE_ACRES = 100

FLOOD_KEYWORDS    = {"Flood", "Flash Flood", "Storm Surge", "Coastal Flood"}
HURRICANE_KEYWORDS = {"Hurricane", "Tropical Storm"}
STORM_KEYWORDS    = {"Severe Thunderstorm", "High Wind", "Extreme Wind"}
TORNADO_KEYWORDS  = {"Tornado"}


def _http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "SandbagSalesTracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _classify_alert(event: str):
    """Return the category bucket for an alert event string."""
    for kw in FLOOD_KEYWORDS:
        if kw.lower() in event.lower():
            return "flood"
    for kw in HURRICANE_KEYWORDS:
        if kw.lower() in event.lower():
            return "hurricane"
    for kw in TORNADO_KEYWORDS:
        if kw.lower() in event.lower():
            return "tornado"
    for kw in STORM_KEYWORDS:
        if kw.lower() in event.lower():
            return "storm"
    return "other"


def fetch_noaa_national():
    """Pull ALL active national NOAA alerts (no state filter) and categorize them."""
    data = _http_json(NOAA_ALERTS_URL)
    if not data:
        return {"total": 0, "flood": 0, "hurricane": 0, "storm": 0, "tornado": 0, "types": []}

    seen_types = set()
    counts = {"flood": 0, "hurricane": 0, "storm": 0, "tornado": 0, "other": 0}
    type_list = []

    for feature in data.get("features", []):
        props = feature.get("properties", {})
        event = props.get("event", "").strip()
        if not event:
            continue
        category = _classify_alert(event)
        counts[category] = counts.get(category, 0) + 1
        if event not in seen_types:
            seen_types.add(event)
            type_list.append(event)

    total = sum(counts.values())
    return {
        "total": total,
        "flood": counts["flood"],
        "hurricane": counts["hurricane"],
        "storm": counts["storm"],
        "tornado": counts["tornado"],
        "types": sorted(type_list),
    }


def fetch_nifc_national():
    """Pull active large wildfires nationally from NIFC WFIGS."""
    qs = urllib.parse.urlencode({
        "where": f"IncidentSize > {MIN_FIRE_ACRES} AND IncidentTypeCategory = 'WF' AND (PercentContained IS NULL OR PercentContained < 90)",
        "outFields": "IncidentName,POOState,IncidentSize,PercentContained",
        "resultRecordCount": 200,
        "f": "json",
    })
    data = _http_json(f"{NIFC_ACTIVE_URL}?{qs}")
    if not data:
        return {"count": 0, "total_acres": 0}

    count = 0
    total_acres = 0.0
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        acres = a.get("IncidentSize")
        if acres:
            count += 1
            total_acres += float(acres)

    return {"count": count, "total_acres": round(total_acres, 0)}


def get_daily_sales(target_date: date):
    """Count sales and revenue for a specific date from the DB."""
    import sqlite3
    db_path = os.getenv("DB_PATH") or os.path.join(os.path.dirname(__file__), "data", "sales.db")
    if not os.path.exists(db_path):
        # fallback for local dev
        db_path = os.path.join(os.path.dirname(__file__), "sales.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    date_str = target_date.isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount),0) as rev FROM sales WHERE date = ?",
        (date_str,)
    ).fetchone()
    totals = conn.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(amount),0) as rev FROM sales WHERE date <= ?",
        (date_str,)
    ).fetchone()
    conn.close()
    return {
        "sales_today": row["cnt"],
        "revenue_today": round(row["rev"], 2),
        "total_sales": totals["cnt"],
        "total_revenue": round(totals["rev"], 2),
    }


def take_snapshot(target_date: "date | None" = None, notes: str = ""):
    target_date = target_date or date.today()
    print(f"[snapshot] Collecting data for {target_date.isoformat()}...")

    noaa = fetch_noaa_national()
    print(f"  NOAA alerts: {noaa['total']} total  flood={noaa['flood']}  hurricane={noaa['hurricane']}  storm={noaa['storm']}  tornado={noaa['tornado']}")

    nifc = fetch_nifc_national()
    print(f"  NIFC fires: {nifc['count']} active, {nifc['total_acres']:,.0f} total acres")

    sales = get_daily_sales(target_date)
    print(f"  Sales today: {sales['sales_today']}  Revenue today: ${sales['revenue_today']}")
    print(f"  All-time totals: {sales['total_sales']} sales, ${sales['total_revenue']} revenue")

    snap = {
        "snapshot_date":   target_date.isoformat(),
        "sales_today":     sales["sales_today"],
        "revenue_today":   sales["revenue_today"],
        "total_sales":     sales["total_sales"],
        "total_revenue":   sales["total_revenue"],
        "noaa_alert_count": noaa["total"],
        "flood_alerts":    noaa["flood"],
        "fire_count":      nifc["count"],
        "fire_acres":      nifc["total_acres"],
        "hurricane_alerts": noaa["hurricane"],
        "storm_alerts":    noaa["storm"],
        "tornado_alerts":  noaa["tornado"],
        "alert_types_json": json.dumps(noaa["types"]),
        "notes":           notes,
        "captured_at":     datetime.now(timezone.utc).isoformat(),
    }

    db_init()
    save_climate_snapshot(snap)
    print(f"  [snapshot] Saved snapshot for {target_date.isoformat()}.")
    return snap


if __name__ == "__main__":
    target = None
    notes = ""
    args = sys.argv[1:]
    if "--date" in args:
        idx = args.index("--date")
        target = date.fromisoformat(args[idx + 1])
    if "--notes" in args:
        idx = args.index("--notes")
        notes = args[idx + 1]
    take_snapshot(target, notes)
