"""
backfill_weather.py — Fetch historical weather for all sales missing weather data.

Uses Open-Meteo archive API (free, no key needed).
Maps each sale's US state to a representative lat/lon (state capital centroid).

Usage:
    python backfill_weather.py            # only fills missing records
    python backfill_weather.py --all      # re-fetches every sale (overwrite)
    python backfill_weather.py --dry-run  # shows what would be fetched, no writes
"""

import sys
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from database import db_init, get_sales_missing_weather, get_all_sales, save_sale_weather

# ── State → (lat, lon) — representative centroid (usually state capital area) ──
STATE_COORDS = {
    "Alabama": (32.361538, -86.279118),
    "Alaska": (58.301935, -134.419740),
    "Arizona": (33.448457, -112.073844),
    "Arkansas": (34.736009, -92.331122),
    "California": (38.555605, -121.468926),
    "Colorado": (39.7391667, -104.984167),
    "Connecticut": (41.767, -72.677),
    "Delaware": (39.161921, -75.526755),
    "Florida": (30.4518, -84.27277),
    "Georgia": (33.76, -84.39),
    "Hawaii": (21.30895, -157.826182),
    "Idaho": (43.613739, -116.237651),
    "Illinois": (39.783250, -89.650373),
    "Indiana": (39.790942, -86.147685),
    "Iowa": (41.590939, -93.620866),
    "Kansas": (39.04, -95.69),
    "Kentucky": (38.197274, -84.86311),
    "Louisiana": (30.45809, -91.140229),
    "Maine": (44.323535, -69.765261),
    "Maryland": (38.972945, -76.501157),
    "Massachusetts": (42.2352, -71.0275),
    "Michigan": (42.7335, -84.5467),
    "Minnesota": (44.95, -93.094),
    "Mississippi": (32.32, -90.207),
    "Missouri": (38.572954, -92.189283),
    "Montana": (46.595805, -112.027031),
    "Nebraska": (40.809868, -96.675345),
    "Nevada": (39.160949, -119.753877),
    "New Hampshire": (43.220093, -71.549127),
    "New Jersey": (40.221741, -74.756138),
    "New Mexico": (35.667231, -105.964575),
    "New York": (42.659829, -73.781339),
    "North Carolina": (35.771, -78.638),
    "North Dakota": (46.813343, -100.779004),
    "Ohio": (39.962245, -83.000647),
    "Oklahoma": (35.482309, -97.534994),
    "Oregon": (44.931109, -123.029159),
    "Pennsylvania": (40.269789, -76.875613),
    "Rhode Island": (41.82355, -71.422132),
    "South Carolina": (34.000, -81.035),
    "South Dakota": (44.367966, -100.336378),
    "Tennessee": (36.165, -86.784),
    "Texas": (30.266667, -97.75),
    "Utah": (40.7547, -111.892622),
    "Vermont": (44.26639, -72.57194),
    "Virginia": (37.54, -77.46),
    "Washington": (47.042418, -122.893077),
    "West Virginia": (38.349497, -81.633294),
    "Wisconsin": (43.074722, -89.384444),
    "Wyoming": (41.145548, -104.802042),
    # DC
    "District Of Columbia": (38.9072, -77.0369),
    "Washington Dc": (38.9072, -77.0369),
}

# WMO weather interpretation codes → human description
WMO_DESC = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ slight hail", 99: "Thunderstorm w/ heavy hail",
}

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_weather(lat: float, lon: float, date_str: str) -> dict | None:
    """Call Open-Meteo archive for a single date. Returns parsed dict or None on failure."""
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": date_str,
        "end_date": date_str,
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean,precipitation_sum,windspeed_10m_max,weathercode",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    })
    url = f"{OPEN_METEO_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "SandbagSalesTracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        daily = data.get("daily", {})
        code = (daily.get("weathercode") or [None])[0]
        return {
            "temp_max_f":        (daily.get("temperature_2m_max") or [None])[0],
            "temp_min_f":        (daily.get("temperature_2m_min") or [None])[0],
            "temp_mean_f":       (daily.get("temperature_2m_mean") or [None])[0],
            "precipitation_in":  (daily.get("precipitation_sum") or [None])[0],
            "windspeed_max_mph": (daily.get("windspeed_10m_max") or [None])[0],
            "weather_code":      code,
            "weather_desc":      WMO_DESC.get(code, f"Code {code}") if code is not None else None,
        }
    except Exception as e:
        print(f"  [!] Fetch failed for {date_str} at ({lat},{lon}): {e}")
        return None


def run(refetch_all: bool = False, dry_run: bool = False):
    db_init()

    if refetch_all:
        sales = [{"id": s["id"], "date": s["date"], "location": s["location"]} for s in get_all_sales()]
        print(f"Re-fetching weather for ALL {len(sales)} sales...")
    else:
        sales = get_sales_missing_weather()
        print(f"Found {len(sales)} sale(s) missing weather data.")

    if not sales:
        print("Nothing to do.")
        return

    ok = 0
    skipped = 0
    for s in sales:
        loc = s["location"].strip().title()
        coords = STATE_COORDS.get(loc)
        if not coords:
            print(f"  [skip] sale {s['id']} ({s['date']}) — unknown location: '{s['location']}'")
            skipped += 1
            continue

        lat, lon = coords
        print(f"  sale {s['id']:>3}  {s['date']}  {loc:<20} lat={lat} lon={lon} ...", end=" ", flush=True)

        if dry_run:
            print("(dry run)")
            continue

        weather = fetch_weather(lat, lon, s["date"])
        if not weather:
            skipped += 1
            continue

        weather["sale_date"] = s["date"]
        weather["location"] = loc
        weather["fetched_at"] = datetime.now(timezone.utc).isoformat()

        save_sale_weather(s["id"], weather)
        print(f"{weather['weather_desc'] or '?'}  {weather['temp_mean_f']}°F  {weather['precipitation_in']}\" precip")
        ok += 1
        time.sleep(0.3)  # be polite to the free API

    print(f"\nDone. {ok} saved, {skipped} skipped.")


if __name__ == "__main__":
    args = sys.argv[1:]
    run(
        refetch_all="--all" in args,
        dry_run="--dry-run" in args,
    )
