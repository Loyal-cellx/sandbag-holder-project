import time
import math
import urllib.request
import urllib.parse
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

# In-memory cache shared by every external feed below.
# Key: (source_tag, sorted_state_codes_key) → {"data": list, "ts": float}
_cache = {}
CACHE_TTL = 30 * 60  # 30 minutes

# ── Multi-hazard data sources (NIFC + NHC) ───────────────────────────────────
NIFC_ACTIVE_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/"
                   "services/WFIGS_Incident_Locations_Current/FeatureServer/0/query")
# Perimeter history has no state attribute, so we use the current-season perimeter
# service (attr_POOState filterable). Fires from this season ARE the burn scars
# that matter for current rain. For multi-year coverage we'd need spatial bbox queries.
NIFC_PERIM_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/"
                  "services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query")
NHC_CURRENT_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"

MIN_FIRE_ACRES = 100              # ignore tiny incidents
CONE_RADIUS_MI = 400              # rough cone-of-uncertainty proximity
FORECAST_HOURS_AHEAD = 72         # NHC track projection window

# Approximate state centroids (lat, lon) — only states plausibly reachable by
# Atlantic/Gulf cyclones (or HI for E. Pacific). Used for hurricane proximity checks.
STATE_CENTROIDS = {
    "FL": (27.8, -81.7), "TX": (31.0, -99.0), "LA": (31.0, -92.0),
    "MS": (32.7, -89.7), "AL": (32.8, -86.8), "GA": (32.7, -83.5),
    "SC": (33.9, -80.9), "NC": (35.5, -79.4), "VA": (37.5, -78.7),
    "MD": (39.0, -76.6), "DE": (39.0, -75.5), "NJ": (40.1, -74.7),
    "NY": (42.9, -75.5), "CT": (41.6, -72.7), "RI": (41.7, -71.5),
    "MA": (42.2, -71.8), "NH": (43.7, -71.6), "ME": (45.4, -69.0),
    "HI": (20.8, -156.3),
}


# ── Generic helpers ──────────────────────────────────────────────────────────

def _get_icon(event):
    for keyword, icon in ALERT_ICONS.items():
        if keyword.lower() in event.lower():
            return icon
    return "⚠️"


def _http_json(url, timeout=5):
    """GET a URL and return parsed JSON, or None on any network/parse failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "SandbagSalesTracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _cached(source_tag, state_codes, fetcher):
    """Cache wrapper keyed by (source_tag, sorted_state_codes). Empty result is still cached."""
    key = (source_tag, ",".join(sorted(state_codes)))
    hit = _cache.get(key)
    if hit and (time.time() - hit["ts"]) < CACHE_TTL:
        return hit["data"]
    data = fetcher(state_codes) if state_codes else []
    _cache[key] = {"data": data, "ts": time.time()}
    return data


def _haversine_mi(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ── NWS alerts ───────────────────────────────────────────────────────────────

def _fetch_nws_alerts(state_codes):
    if not state_codes:
        return []
    area = ",".join(state_codes)
    data = _http_json(f"https://api.weather.gov/alerts/active?area={area}&status=actual")
    if not data:
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
    return _cached("nws", state_codes, _fetch_nws_alerts)


# ── NIFC active wildfires ────────────────────────────────────────────────────

def _wfigs_state_clause(state_codes):
    """Build a SQL IN-clause against POOState formatted as 'US-XX'."""
    quoted = ",".join(f"'US-{c}'" for c in state_codes)
    return f"POOState IN ({quoted})"


def _fetch_nifc_active(state_codes):
    """Active large wildfires (>100 ac) in customer states. Sorted by acres desc, top 5."""
    where = (f"({_wfigs_state_clause(state_codes)}) "
             f"AND IncidentSize > {MIN_FIRE_ACRES} "
             f"AND IncidentTypeCategory = 'WF' "
             f"AND (PercentContained IS NULL OR PercentContained < 90)")
    qs = urllib.parse.urlencode({
        "where": where,
        "outFields": "IncidentName,POOState,IncidentSize,PercentContained,FireDiscoveryDateTime",
        "orderByFields": "IncidentSize DESC",
        "resultRecordCount": 5,
        "f": "json",
    })
    data = _http_json(f"{NIFC_ACTIVE_URL}?{qs}")
    if not data:
        return []
    fires = []
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        state = (a.get("POOState") or "").replace("US-", "")
        acres = a.get("IncidentSize")
        if not state or not acres:
            continue
        fires.append({
            "state": state,
            "name": a.get("IncidentName") or "Unnamed",
            "acres": int(acres),
            "contained": int(a.get("PercentContained") or 0),
        })
    return fires


# ── NIFC fire perimeters (burn-scar proxy, current fire season) ──────────────

def _fetch_nifc_burn_scars(state_codes):
    """Largest fire perimeter per state from the current season — proxy for active burn scars."""
    # Perimeters service uses attr_-prefixed field names.
    quoted = ",".join(f"'US-{c}'" for c in state_codes)
    where = f"attr_POOState IN ({quoted}) AND poly_GISAcres > {MIN_FIRE_ACRES}"
    qs = urllib.parse.urlencode({
        "where": where,
        "outFields": "attr_IncidentName,attr_POOState,poly_GISAcres,attr_FireDiscoveryDateTime",
        "orderByFields": "poly_GISAcres DESC",
        "resultRecordCount": 200,
        "f": "json",
    })
    data = _http_json(f"{NIFC_PERIM_URL}?{qs}")
    if not data:
        return []
    by_state = {}
    for feat in data.get("features", []):
        a = feat.get("attributes", {})
        st = (a.get("attr_POOState") or "").replace("US-", "")
        acres = a.get("poly_GISAcres")
        if not st or not acres:
            continue
        # Already sorted by acres desc, so the first one we see per state wins.
        if st in by_state:
            continue
        ts = a.get("attr_FireDiscoveryDateTime")
        year = None
        if ts:
            try:
                year = int(time.strftime("%Y", time.gmtime(ts / 1000)))
            except (TypeError, ValueError, OSError):
                year = None
        by_state[st] = {
            "state": st,
            "name": a.get("attr_IncidentName") or "Unnamed",
            "acres": int(acres),
            "year": year,
        }
    return list(by_state.values())


# ── NHC active tropical cyclones ─────────────────────────────────────────────

CLASS_LABELS = {
    "HU": "Hurricane",
    "TS": "Tropical Storm",
    "TD": "Tropical Depression",
    "PT": "Post-Tropical",
    "STS": "Subtropical Storm",
    "STD": "Subtropical Depression",
}


def _project_track(lat, lon, dir_deg, speed_kt, hours_ahead):
    """Project a chain of (lat, lon) points forward every 12h using current motion vector.
    Returns the current position alone if motion vector is missing/zero."""
    points = [(lat, lon)]
    if dir_deg is None or speed_kt is None or speed_kt <= 0:
        return points
    bearing = math.radians(dir_deg)
    for h in range(12, hours_ahead + 1, 12):
        nautical_miles = speed_kt * h            # kt × hours = nm
        statute_miles = nautical_miles * 1.15078
        # Approximate flat-earth offset; fine at <1500 mi for a UI heuristic.
        d_lat = (statute_miles * math.cos(bearing)) / 69.0
        d_lon = (statute_miles * math.sin(bearing)) / (69.0 * max(0.2, math.cos(math.radians(lat))))
        points.append((lat + d_lat, lon + d_lon))
    return points


def _fetch_nhc_storms(customer_state_codes):
    """Active named storms whose current track passes within CONE_RADIUS_MI of a customer state centroid."""
    data = _http_json(NHC_CURRENT_URL)
    if not data:
        return []
    relevant_centroids = {c: STATE_CENTROIDS[c]
                          for c in customer_state_codes if c in STATE_CENTROIDS}
    if not relevant_centroids:
        return []
    storms = []
    for s in data.get("activeStorms") or []:
        try:
            lat = float(s.get("latitudeNumeric"))
            lon = float(s.get("longitudeNumeric"))
        except (TypeError, ValueError):
            continue
        try:
            speed = float(s.get("movementSpeed")) if s.get("movementSpeed") not in (None, "") else None
        except (TypeError, ValueError):
            speed = None
        try:
            heading = float(s.get("movementDir")) if s.get("movementDir") not in (None, "") else None
        except (TypeError, ValueError):
            heading = None
        track = _project_track(lat, lon, heading, speed, FORECAST_HOURS_AHEAD)

        affected = []
        for st, (clat, clon) in relevant_centroids.items():
            min_dist = min(_haversine_mi(p[0], p[1], clat, clon) for p in track)
            if min_dist <= CONE_RADIUS_MI:
                affected.append(st)
        if not affected:
            continue
        cls = (s.get("classification") or "").upper()
        try:
            wind_kt = int(float(s.get("intensity"))) if s.get("intensity") else None
        except (TypeError, ValueError):
            wind_kt = None
        storms.append({
            "name": s.get("name") or "Unnamed",
            "classification": cls,
            "class_label": CLASS_LABELS.get(cls, cls or "Cyclone"),
            "wind_kt": wind_kt,
            "affected_states": affected,
        })
    return storms


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


def _score_combo(alerts, fires, burn_scars):
    """0–10 pts: a flood watch/warning over a state with active fire or fresh burn scar.
    This is the only path through which fires affect the score — pure wildfires don't sell sandbags."""
    fire_states = {f["state"] for f in fires} | {b["state"] for b in burn_scars}
    if not fire_states:
        return 0, []
    pts = 0
    triggered = []
    for a in alerts:
        ev = (a.get("event") or "").lower()
        if "flood" not in ev:
            continue
        # _fetch_nws_alerts already extracts a 2-letter code from areaDesc.
        st_code = (a.get("state") or "").upper()
        if st_code and st_code in fire_states:
            pts += 5
            triggered.append(st_code)
    return min(pts, 10), sorted(set(triggered))


def _score_nhc(storms):
    """0–12 pts: each active cyclone with cone over a customer state, +bonus for hurricane class."""
    pts = 0
    for s in storms:
        pts += 6
        if s.get("classification") == "HU":
            pts += 2
    return min(pts, 12)


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
    fires = _cached("nifc_active", state_codes, _fetch_nifc_active)
    burn_scars = _cached("nifc_scars", state_codes, _fetch_nifc_burn_scars)
    storms = _cached("nhc", state_codes, _fetch_nhc_storms)

    month = date.today().month
    season_pts, season_label = _score_season(month, state_codes)

    time_pts = _score_time(days_since, wma_freq)
    weather_pts = _score_weather(alerts)
    combo_pts, combo_states = _score_combo(alerts, fires, burn_scars)
    nhc_pts = _score_nhc(storms)
    score = min(time_pts + weather_pts + season_pts + combo_pts + nhc_pts, 99)

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
        "fires": fires,
        "burn_scars": burn_scars,
        "storms": storms,
        "combo_pts": combo_pts,
        "combo_states": combo_states,
        "nhc_pts": nhc_pts,
    }
