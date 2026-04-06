import time
import urllib.request
import urllib.error
import json
from datetime import date

# State name → postal code for NWS API
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

# States on the Gulf/Atlantic coast that are hurricane-exposed
HURRICANE_STATES = {"FL", "TX", "NC", "SC", "LA", "GA", "AL", "MS", "VA",
                    "NY", "NJ", "CT", "MA", "RI", "DE", "MD", "ME", "NH"}

# Northern states with significant spring snowmelt/thaw flooding
SNOWMELT_STATES = {"AK", "MN", "WI", "MI", "ND", "SD", "MT", "WY", "ID",
                   "VT", "NH", "ME", "NY", "WA", "OR", "CO"}

# Monthly general activity baseline (higher = warmer/more active storm weather)
MONTHLY_BASELINE = {1: 1, 2: 2, 3: 5, 4: 7, 5: 8, 6: 9,
                    7: 10, 8: 10, 9: 9, 10: 7, 11: 4, 12: 2}

# Hurricane season monthly boost (applied to hurricane-exposed states only)
HURRICANE_MONTHLY = {6: 6, 7: 8, 8: 10, 9: 10, 10: 8, 11: 6}

# Spring snowmelt boost by month (applied to northern states)
SNOWMELT_MONTHLY = {3: 4, 4: 8, 5: 6}
# Alaska peaks later in the melt season
ALASKA_MELT_MONTHLY = {4: 10, 5: 10, 3: 5}

# Relevance scores for NWS alert types (max weather component = 35 pts)
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

# In-memory NWS cache: {state_codes_key: {"alerts": [...], "ts": float}}
_cache = {}
CACHE_TTL = 30 * 60  # 30 minutes


# ── Weather helpers ──────────────────────────────────────────────────────────

def _get_icon(event):
    for keyword, icon in ALERT_ICONS.items():
        if keyword.lower() in event.lower():
            return icon
    return "⚠️"


def _fetch_nws_alerts(state_codes):
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


# ── Scoring functions ────────────────────────────────────────────────────────

def _weighted_moving_avg(gaps):
    """Exponentially-weighted moving average of sale gaps.
    Most recent gap gets the highest weight. Returns None if < 2 gaps."""
    if not gaps:
        return None
    # weights: 1, 1.5, 2, 2.5, ... (oldest to newest)
    weights = [1.0 + 0.5 * i for i in range(len(gaps))]
    wma = sum(g * w for g, w in zip(gaps, weights)) / sum(weights)
    return round(wma, 1)


def _score_time(days_since, wma_freq):
    """0–45 pts based on days elapsed vs weighted moving average frequency."""
    if days_since is None or not wma_freq or wma_freq == 0:
        return 0
    ratio = days_since / wma_freq
    if ratio <= 1.0:
        return round(ratio * 30)
    else:
        return round(30 + min((ratio - 1.0) / 0.5, 1.0) * 15)


def _score_weather(alerts):
    """0–35 pts based on active NWS severe weather alerts."""
    return min(sum(a["score"] for a in alerts), 35)


def _score_season(month, state_codes):
    """0–20 pts based on month + state-specific seasonal factors.
    Returns (pts, label) tuple."""
    state_set = set(state_codes)

    # Hurricane component: applies to coastal/Gulf states
    hurricane_pts = 0
    if state_set & HURRICANE_STATES:
        hurricane_pts = HURRICANE_MONTHLY.get(month, 0)

    # Snowmelt/spring thaw component
    melt_pts = 0
    if "AK" in state_set:
        melt_pts = max(melt_pts, ALASKA_MELT_MONTHLY.get(month, 0))
    if state_set & SNOWMELT_STATES:
        melt_pts = max(melt_pts, SNOWMELT_MONTHLY.get(month, 0))

    # General warm-weather baseline (applies everywhere)
    baseline_pts = MONTHLY_BASELINE.get(month, 1)

    # Take the highest applicable factor (they describe the same demand driver)
    seasonal_pts = max(hurricane_pts, melt_pts, baseline_pts)
    capped = min(seasonal_pts, 20)

    # Pick the most descriptive label
    if hurricane_pts >= melt_pts and hurricane_pts > 0:
        label = "Hurricane Season"
    elif melt_pts >= 8 and "AK" in state_set:
        label = "Alaska Snowmelt"
    elif melt_pts > 0:
        label = "Spring Flood Season"
    elif month in (6, 7, 8, 9):
        label = "Storm Season"
    elif month in (3, 4, 5):
        label = "Spring Rain Season"
    else:
        label = None

    return capped, label


# ── Main entry point ─────────────────────────────────────────────────────────

def get_prediction(stats, locations):
    """
    Returns a prediction dict:
      score         int 0–99
      label         "LOW" | "MEDIUM" | "HIGH"
      color         CSS color string
      days_since    int | None
      wma_freq      float | None   (weighted moving avg gap)
      avg_freq      float | None   (simple avg for display comparison)
      eta_days      float | None
      alerts        list of alert dicts
      season_pts    int
      season_label  str | None
    """
    days_since = stats.get("days_since_last_sale")
    avg_freq = stats.get("avg_days_between_sales")
    gaps = stats.get("sale_gaps", [])

    wma_freq = _weighted_moving_avg(gaps) or avg_freq

    state_codes = []
    for loc in locations:
        code = STATE_CODES.get(loc)
        if code and code not in state_codes:
            state_codes.append(code)

    alerts = _get_cached_alerts(state_codes)

    month = date.today().month
    season_pts, season_label = _score_season(month, state_codes)

    time_pts = _score_time(days_since, wma_freq)
    weather_pts = _score_weather(alerts)
    score = min(time_pts + weather_pts + season_pts, 99)

    if score <= 30:
        label, color = "LOW", "#64748b"
    elif score <= 60:
        label, color = "MEDIUM", "#f97316"
    else:
        label, color = "HIGH", "#4ade80"

    eta_days = max(0, round((wma_freq or 0) - (days_since or 0), 1)) if wma_freq and days_since is not None else None

    return {
        "score": score,
        "label": label,
        "color": color,
        "days_since": days_since,
        "wma_freq": wma_freq,
        "avg_freq": avg_freq,
        "eta_days": eta_days,
        "alerts": alerts,
        "season_pts": season_pts,
        "season_label": season_label,
    }
