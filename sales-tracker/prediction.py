import time
import math
import urllib.request
import urllib.parse
import urllib.error
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

# ── State reference data ──────────────────────────────────────────────────────

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

# Geo-weights based on actual historical sales share.
# PREMIUM = top 3 states by order volume (FL 20%, TX 9%, AK 7%).
# ACTIVE = states with 2+ historical orders. All others default to 1.0.
# These amplify alert scores for states where sandbag buyers actually live.
GEO_WEIGHT = {
    "FL": 2.2, "TX": 1.8, "AK": 1.6,         # premium: proven high-volume markets
    "CA": 1.3, "GA": 1.3, "CO": 1.3, "WI": 1.3,
    "MI": 1.2, "MT": 1.2, "OK": 1.2, "NC": 1.2,
}

# Monthly general activity baseline (higher = warmer / more active storm weather)
MONTHLY_BASELINE = {1: 1, 2: 2, 3: 5, 4: 7, 5: 8, 6: 9,
                    7: 10, 8: 10, 9: 9, 10: 7, 11: 4, 12: 2}

# Hurricane season monthly boost (applied to hurricane-exposed states only)
HURRICANE_MONTHLY = {6: 6, 7: 8, 8: 10, 9: 10, 10: 8, 11: 6}

# Spring snowmelt boost by month (applied to northern states)
SNOWMELT_MONTHLY = {3: 4, 4: 8, 5: 6}
ALASKA_MELT_MONTHLY = {4: 10, 5: 10, 3: 5}

# Alert scores — only flood/hurricane/surge type alerts move sandbag demand.
# Tornado/thunderstorm alerts are low value for this product; scored low intentionally.
ALERT_SCORES = {
    "Flood Warning": 14,
    "Flash Flood Warning": 14,
    "Hurricane Warning": 11,
    "Tropical Storm Warning": 11,
    "Storm Surge Warning": 9,
    "Coastal Flood Warning": 8,
    "Flood Watch": 8,
    "Flash Flood Watch": 8,
    "Hurricane Watch": 6,
    "Tropical Storm Watch": 6,
    "Storm Surge Watch": 5,
    "Coastal Flood Watch": 4,
    "High Wind Warning": 3,
    "Extreme Wind Warning": 3,
    "Severe Thunderstorm Warning": 2,
    "Tornado Warning": 2,
    "High Wind Watch": 2,
    "Severe Thunderstorm Watch": 1,
    "Tornado Watch": 1,
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
_cache = {}
CACHE_TTL = 30 * 60  # 30 minutes


# ── NIFC / NHC endpoint constants ──────────────────────────────────────────────

NIFC_ACTIVE_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/"
                   "services/WFIGS_Incident_Locations_Current/FeatureServer/0/query")
NIFC_PERIM_URL  = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/"
                   "services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query")
NHC_CURRENT_URL  = "https://www.nhc.noaa.gov/CurrentStorms.json"
USGS_FLOOD_URL   = "https://waterwatch.usgs.gov/webservices/floodstage?format=json"

MIN_FIRE_ACRES    = 100
CONE_RADIUS_MI    = 400
FORECAST_HOURS_AHEAD = 72

STATE_CENTROIDS = {
    "FL": (27.8, -81.7), "TX": (31.0, -99.0), "LA": (31.0, -92.0),
    "MS": (32.7, -89.7), "AL": (32.8, -86.8), "GA": (32.7, -83.5),
    "SC": (33.9, -80.9), "NC": (35.5, -79.4), "VA": (37.5, -78.7),
    "MD": (39.0, -76.6), "DE": (39.0, -75.5), "NJ": (40.1, -74.7),
    "NY": (42.9, -75.5), "CT": (41.6, -72.7), "RI": (41.7, -71.5),
    "MA": (42.2, -71.8), "NH": (43.7, -71.6), "ME": (45.4, -69.0),
    "HI": (20.8, -156.3),
}


# ── Generic helpers ────────────────────────────────────────────────────────────

def _get_icon(event):
    for keyword, icon in ALERT_ICONS.items():
        if keyword.lower() in event.lower():
            return icon
    return "⚠️"


def _http_json(url, timeout=6):
    """GET a URL, return parsed JSON, or None on any failure. Reads in chunks to handle large responses."""
    req = urllib.request.Request(url, headers={"User-Agent": "SandbagSalesTracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks = []
            while True:
                chunk = resp.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                chunks.append(chunk)
            return json.loads(b"".join(chunks))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _cached(source_tag, state_codes, fetcher):
    """Cache wrapper keyed by (source_tag, sorted_state_codes). Empty results are cached."""
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


# ── NWS alerts ─────────────────────────────────────────────────────────────────

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
        base_score = ALERT_SCORES.get(event, 0)
        geo_mult = GEO_WEIGHT.get(state, 1.0)
        alerts.append({
            "event": event,
            "state": state,
            "icon": _get_icon(event),
            "score": base_score,
            "geo_mult": round(geo_mult, 2),
            "weighted_score": round(base_score * geo_mult, 1),
        })
    return alerts


def _get_cached_alerts(state_codes):
    return _cached("nws", state_codes, _fetch_nws_alerts)


# ── USGS WaterWatch flood stage ─────────────────────────────────────────────────

# FIPS state codes → postal codes for filtering USGS gauge data
FIPS_TO_POSTAL = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "12": "FL", "13": "GA",
    "15": "HI", "16": "ID", "17": "IL", "18": "IN", "19": "IA",
    "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO",
    "30": "MT", "31": "NE", "32": "NV", "33": "NH", "34": "NJ",
    "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC",
    "46": "SD", "47": "TN", "48": "TX", "49": "UT", "50": "VT",
    "51": "VA", "53": "WA", "54": "WV", "55": "WI", "56": "WY",
}

# Severity weight per flood category
FLOOD_STAGE_WEIGHT = {
    "action": 0.5,
    "flood":  1.5,
    "major":  2.5,
    "record": 4.0,
}


def _fetch_usgs_flood(customer_state_codes):
    """
    Return a dict summarizing USGS gauges at/above action stage in customer states.
    Weighted by severity (action=0.5, flood=1.5, major=2.5, record=4.0) and geo-weight.
    Returns: {"by_state": {state: {count, weight, peak_category}}, "total_weight": float}
    """
    data = _http_json(USGS_FLOOD_URL, timeout=8)
    if not data or "site" not in data:
        return {"by_state": {}, "total_weight": 0.0}

    by_state = {}
    for site in data.get("site", []):
        # USGS WaterWatch returns state as site_no first 2 digits (FIPS) for some formats
        # or as a 'state' field depending on version. Try both.
        state_raw = (site.get("state") or "").upper()
        if not state_raw:
            # Try FIPS prefix from site_no
            site_no = site.get("site_no", "")
            fips = site_no[:2] if len(site_no) >= 2 else ""
            state_raw = FIPS_TO_POSTAL.get(fips, "")

        if state_raw not in customer_state_codes:
            continue

        flood_stage = (site.get("flood_stage") or "").lower()
        if flood_stage not in FLOOD_STAGE_WEIGHT:
            continue

        severity_w = FLOOD_STAGE_WEIGHT[flood_stage]
        geo_w = GEO_WEIGHT.get(state_raw, 1.0)
        weighted = severity_w * geo_w

        if state_raw not in by_state:
            by_state[state_raw] = {"count": 0, "weight": 0.0, "peak_category": "action"}
        by_state[state_raw]["count"] += 1
        by_state[state_raw]["weight"] += weighted
        # Track peak severity
        order = ["action", "flood", "major", "record"]
        if order.index(flood_stage) > order.index(by_state[state_raw]["peak_category"]):
            by_state[state_raw]["peak_category"] = flood_stage

    total_weight = sum(v["weight"] for v in by_state.values())
    return {"by_state": by_state, "total_weight": total_weight}


def _get_cached_usgs(state_codes):
    return _cached("usgs_flood", state_codes, _fetch_usgs_flood)


# ── NIFC active wildfires ───────────────────────────────────────────────────────

def _wfigs_state_clause(state_codes):
    quoted = ",".join(f"'US-{c}'" for c in state_codes)
    return f"POOState IN ({quoted})"


def _fetch_nifc_active(state_codes):
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


# ── NIFC fire perimeters (burn-scar proxy) ─────────────────────────────────────

def _fetch_nifc_burn_scars(state_codes):
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


# ── NHC active tropical cyclones ───────────────────────────────────────────────

CLASS_LABELS = {
    "HU": "Hurricane",
    "TS": "Tropical Storm",
    "TD": "Tropical Depression",
    "PT": "Post-Tropical",
    "STS": "Subtropical Storm",
    "STD": "Subtropical Depression",
}


def _project_track(lat, lon, dir_deg, speed_kt, hours_ahead):
    points = [(lat, lon)]
    if dir_deg is None or speed_kt is None or speed_kt <= 0:
        return points
    bearing = math.radians(dir_deg)
    for h in range(12, hours_ahead + 1, 12):
        nautical_miles = speed_kt * h
        statute_miles = nautical_miles * 1.15078
        d_lat = (statute_miles * math.cos(bearing)) / 69.0
        d_lon = (statute_miles * math.sin(bearing)) / (69.0 * max(0.2, math.cos(math.radians(lat))))
        points.append((lat + d_lat, lon + d_lon))
    return points


def _fetch_nhc_storms(customer_state_codes):
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


# ── Scoring functions ──────────────────────────────────────────────────────────

def _weighted_moving_avg(gaps):
    """
    Exponentially-weighted moving average of sale gaps.
    Filters out 0-day gaps first — same-day multi-sales aren't meaningful intervals.
    Most recent gap gets highest weight. Returns None if < 2 valid gaps.
    """
    clean = [g for g in gaps if g > 0]
    if len(clean) < 2:
        return None
    weights = [1.0 + 0.5 * i for i in range(len(clean))]
    wma = sum(g * w for g, w in zip(clean, weights)) / sum(weights)
    return round(wma, 1)


def _score_time(days_since, wma_freq):
    """0-45 pts based on days elapsed vs weighted moving average frequency."""
    if days_since is None or not wma_freq or wma_freq == 0:
        return 0
    ratio = days_since / wma_freq
    if ratio <= 1.0:
        return round(ratio * 30)
    else:
        return round(30 + min((ratio - 1.0) / 0.5, 1.0) * 15)


def _score_weather(alerts):
    """
    0-35 pts based on active NWS severe weather alerts, geo-weighted by customer state.
    Uses weighted_score (base_score × geo_mult) instead of raw score.
    """
    return min(round(sum(a["weighted_score"] for a in alerts)), 35)


def _score_usgs(usgs_data):
    """
    0-10 pts based on USGS gauges at/above action stage in customer states.
    total_weight already incorporates severity and geo-weight.
    Scale: weight 5 → 5 pts, weight 10 → 10 pts, capped at 10.
    """
    total_w = usgs_data.get("total_weight", 0.0)
    return min(round(total_w / 2.0), 10)


def _score_combo(alerts, fires, burn_scars):
    """0-10 pts: flood alert over a state with active fire or fresh burn scar."""
    fire_states = {f["state"] for f in fires} | {b["state"] for b in burn_scars}
    if not fire_states:
        return 0, []
    pts = 0
    triggered = []
    for a in alerts:
        ev = (a.get("event") or "").lower()
        if "flood" not in ev:
            continue
        st_code = (a.get("state") or "").upper()
        if st_code and st_code in fire_states:
            pts += 5
            triggered.append(st_code)
    return min(pts, 10), sorted(set(triggered))


def _score_nhc(storms):
    """0-12 pts: each active cyclone with cone over a customer state."""
    pts = 0
    for s in storms:
        pts += 6
        if s.get("classification") == "HU":
            pts += 2
    return min(pts, 12)


def _score_season(month, state_codes):
    """0-20 pts based on month + state-specific seasonal factors."""
    state_set = set(state_codes)

    hurricane_pts = 0
    if state_set & HURRICANE_STATES:
        hurricane_pts = HURRICANE_MONTHLY.get(month, 0)

    melt_pts = 0
    if "AK" in state_set:
        melt_pts = max(melt_pts, ALASKA_MELT_MONTHLY.get(month, 0))
    if state_set & SNOWMELT_STATES:
        melt_pts = max(melt_pts, SNOWMELT_MONTHLY.get(month, 0))

    baseline_pts = MONTHLY_BASELINE.get(month, 1)
    seasonal_pts = max(hurricane_pts, melt_pts, baseline_pts)
    capped = min(seasonal_pts, 20)

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


def _score_velocity(stats):
    """
    0-15 pts based on recent sales momentum.
    Compares rolling_30_count vs the historical daily rate × 30.
    Uses avg_days_between_sales to compute historical rate (avoids needing first_sale_date).
    Accelerating trend = higher score. Declining = lower.
    """
    rolling_30   = stats.get("rolling_30_count") or 0
    total_sales  = stats.get("total_sales") or 0
    avg_gap      = stats.get("avg_days_between_sales")  # avg days between sales

    if total_sales < 5 or not avg_gap or avg_gap <= 0:
        return 0

    # Historical rate: 1 sale every avg_gap days
    historical_daily = 1.0 / avg_gap
    expected_30 = historical_daily * 30   # ~6.8 sales/30 days at 4.4-day avg gap

    if expected_30 <= 0:
        return 0

    ratio = rolling_30 / expected_30
    # ratio 1.0 = on historical pace → 7 pts
    # ratio 2.0+ = double pace → 15 pts
    # ratio 0.5 or less → 0 pts
    if ratio <= 0.5:
        return 0
    elif ratio <= 1.0:
        return round((ratio - 0.5) / 0.5 * 7)
    else:
        return min(round(7 + (ratio - 1.0) / 1.0 * 8), 15)


def _score_dow(today_date=None):
    """
    0-5 pts based on day of week.
    Mon/Tue/Wed/Thu are historically 3x stronger than Fri/Sat/Sun.
    """
    d = today_date or date.today()
    # weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    if d.weekday() <= 3:   # Mon-Thu
        return 4
    return 0


# ── Main entry point ───────────────────────────────────────────────────────────

def get_prediction(stats, locations):
    """
    Returns a prediction dict:
      score           int 0-99
      label           "LOW" | "MEDIUM" | "HIGH"
      color           CSS color string
      days_since      int | None
      wma_freq        float | None   (weighted moving avg gap, 0-gaps filtered)
      avg_freq        float | None   (simple avg for display comparison)
      eta_days        float | None
      alerts          list of alert dicts (now include weighted_score + geo_mult)
      season_pts      int
      season_label    str | None
      velocity_pts    int
      gauge_pts       int
      dow_pts         int
      usgs_data       dict  (by_state breakdown)
    """
    days_since = stats.get("days_since_last_sale")
    avg_freq   = stats.get("avg_days_between_sales")
    gaps       = stats.get("sale_gaps", [])

    wma_freq = _weighted_moving_avg(gaps) or avg_freq

    state_codes = []
    for loc in locations:
        code = STATE_CODES.get(loc)
        if code and code not in state_codes:
            state_codes.append(code)

    # Fan 5 independent external feeds out concurrently (cold cache ~6s worst case)
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_alerts = pool.submit(_get_cached_alerts, state_codes)
        f_fires  = pool.submit(_cached, "nifc_active", state_codes, _fetch_nifc_active)
        f_scars  = pool.submit(_cached, "nifc_scars",  state_codes, _fetch_nifc_burn_scars)
        f_storms = pool.submit(_cached, "nhc",          state_codes, _fetch_nhc_storms)
        f_usgs   = pool.submit(_get_cached_usgs,        state_codes)

    alerts    = f_alerts.result()
    fires     = f_fires.result()
    burn_scars = f_scars.result()
    storms    = f_storms.result()
    usgs_data = f_usgs.result()
    if not isinstance(usgs_data, dict):
        # _cached's generic empty fallback is a list, but the scorer needs this shape
        usgs_data = {"by_state": {}, "total_weight": 0.0}

    month = date.today().month

    time_pts          = _score_time(days_since, wma_freq)
    weather_pts       = _score_weather(alerts)
    season_pts, season_label = _score_season(month, state_codes)
    combo_pts, combo_states  = _score_combo(alerts, fires, burn_scars)
    nhc_pts           = _score_nhc(storms)
    velocity_pts      = _score_velocity(stats)
    gauge_pts         = _score_usgs(usgs_data)
    dow_pts           = _score_dow()

    score = min(
        time_pts + weather_pts + season_pts + combo_pts +
        nhc_pts + velocity_pts + gauge_pts + dow_pts,
        99
    )

    # Recalibrated thresholds:
    # LOW (0-25): slow period, no weather pressure, off-season
    # MEDIUM (26-55): normal pace + some weather signals
    # HIGH (56+): strong weather events hitting customer states, or major storm + accelerating pace
    if score <= 25:
        label, color = "LOW", "#64748b"
    elif score <= 55:
        label, color = "MEDIUM", "#f97316"
    else:
        label, color = "HIGH", "#4ade80"

    # ETA: how many days until the WMA-predicted next sale (capped at 0)
    eta_days = (
        max(0, round((wma_freq or 0) - (days_since or 0), 1))
        if wma_freq and days_since is not None
        else None
    )

    return {
        "score":        score,
        "label":        label,
        "color":        color,
        "days_since":   days_since,
        "wma_freq":     wma_freq,
        "avg_freq":     avg_freq,
        "eta_days":     eta_days,
        "alerts":       alerts,
        "time_pts":     time_pts,
        "weather_pts":  weather_pts,
        "season_pts":   season_pts,
        "season_label": season_label,
        "velocity_pts": velocity_pts,
        "gauge_pts":    gauge_pts,
        "dow_pts":      dow_pts,
        "fires":        fires,
        "burn_scars":   burn_scars,
        "storms":       storms,
        "combo_pts":    combo_pts,
        "combo_states": combo_states,
        "nhc_pts":      nhc_pts,
        "usgs_data":    usgs_data,
    }
