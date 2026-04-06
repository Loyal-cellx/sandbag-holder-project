import time
import urllib.request
import urllib.error
import json

# State name → FIPS/postal code for NWS API
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}

# Relevance scores for alert types (max weather component = 40 pts)
ALERT_SCORES = {
    "Flood Warning": 12,
    "Flash Flood Warning": 12,
    "Hurricane Warning": 10,
    "Tropical Storm Warning": 10,
    "Storm Surge Warning": 8,
    "Coastal Flood Warning": 7,
    "High Wind Warning": 6,
    "Extreme Wind Warning": 6,
    "Severe Thunderstorm Warning": 4,
    "Tornado Warning": 4,
    # Watch variants = half points
    "Flood Watch": 6,
    "Flash Flood Watch": 6,
    "Hurricane Watch": 5,
    "Tropical Storm Watch": 5,
    "Storm Surge Watch": 4,
    "Coastal Flood Watch": 3,
    "High Wind Watch": 3,
    "Severe Thunderstorm Watch": 2,
    "Tornado Watch": 2,
}

# Alert display icons
ALERT_ICONS = {
    "Flood": "🌊",
    "Flash Flood": "🌊",
    "Hurricane": "🌀",
    "Tropical Storm": "🌀",
    "Storm Surge": "🌊",
    "Coastal Flood": "🌊",
    "High Wind": "💨",
    "Extreme Wind": "💨",
    "Severe Thunderstorm": "⛈",
    "Tornado": "🌪",
}

# In-memory cache: {state_codes_key: {"alerts": [...], "ts": float}}
_cache = {}
CACHE_TTL = 30 * 60  # 30 minutes


def _get_icon(event):
    for keyword, icon in ALERT_ICONS.items():
        if keyword.lower() in event.lower():
            return icon
    return "⚠️"


def _fetch_nws_alerts(state_codes):
    """Fetch active NWS alerts for the given state codes list. Returns list of alert dicts."""
    if not state_codes:
        return []
    area = ",".join(state_codes)
    url = f"https://api.weather.gov/alerts/active?area={area}&status=actual"
    req = urllib.request.Request(url, headers={"User-Agent": "SandbagSalesTracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []

    alerts = []
    seen = set()
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        event = props.get("event", "")
        if event not in ALERT_SCORES:
            continue
        area_desc = props.get("areaDesc", "")
        # Derive state from areaDesc (e.g. "Dade, FL") or use first affected state
        state = ""
        for part in area_desc.split(";"):
            part = part.strip()
            if ", " in part:
                state = part.split(", ")[-1].strip()
                break
        key = (event, state)
        if key in seen:
            continue
        seen.add(key)
        alerts.append({
            "event": event,
            "state": state,
            "icon": _get_icon(event),
            "score": ALERT_SCORES.get(event, 0),
        })

    return alerts


def _get_cached_alerts(state_codes):
    cache_key = ",".join(sorted(state_codes))
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached["ts"]) < CACHE_TTL:
        return cached["alerts"]
    alerts = _fetch_nws_alerts(state_codes)
    _cache[cache_key] = {"alerts": alerts, "ts": time.time()}
    return alerts


def _score_weather(alerts):
    total = sum(a["score"] for a in alerts)
    return min(total, 40)


def _score_time(days_since, avg_freq):
    if days_since is None or avg_freq is None or avg_freq == 0:
        return 0
    ratio = days_since / avg_freq
    # 0–1x avg → 0–40 pts, 1–1.5x avg → 40–60 pts
    if ratio <= 1.0:
        return round(ratio * 40)
    else:
        return round(40 + min((ratio - 1.0) / 0.5, 1.0) * 20)


def get_prediction(stats, locations):
    """
    Returns a prediction dict:
      score       int 0–99
      label       "LOW" | "MEDIUM" | "HIGH"
      color       CSS color string
      days_since  int | None
      avg_freq    float | None
      eta_days    float | None  (estimated days until next sale)
      alerts      list of alert dicts
      weather_ok  bool  (False if NWS was unreachable)
    """
    days_since = stats.get("days_since_last_sale")
    avg_freq = stats.get("avg_days_between_sales")

    # Map location names to state codes
    state_codes = []
    for loc in locations:
        code = STATE_CODES.get(loc)
        if code and code not in state_codes:
            state_codes.append(code)

    alerts = _get_cached_alerts(state_codes)
    weather_ok = True  # cache miss returns [] but we can't distinguish fail from empty easily

    time_pts = _score_time(days_since, avg_freq)
    weather_pts = _score_weather(alerts)
    score = min(time_pts + weather_pts, 99)

    if score <= 30:
        label, color = "LOW", "#64748b"
    elif score <= 60:
        label, color = "MEDIUM", "#f97316"
    else:
        label, color = "HIGH", "#4ade80"

    if avg_freq and days_since is not None:
        eta_days = max(0, round(avg_freq - days_since, 1))
    else:
        eta_days = None

    return {
        "score": score,
        "label": label,
        "color": color,
        "days_since": days_since,
        "avg_freq": avg_freq,
        "eta_days": eta_days,
        "alerts": alerts,
        "weather_ok": weather_ok,
    }
