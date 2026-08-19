"""
TUFTLER sales analytics.

Computes the breakdowns from the July 2026 teardown straight off the
sales-tracker SQLite DB. Standard library only, so nothing new to install
on the Pi and nothing to compile for ARM.

    from tuftler_analytics import analytics_bp, compute
    app.register_blueprint(analytics_bp)

Routes it adds:
    GET /analytics            rendered panel (Jinja)
    GET /api/analytics.json   same numbers as JSON

If the panel comes back empty, run this file directly to see what the
loader actually found:

    python3 tuftler_analytics.py --db /data/sales.db --inspect
"""

import argparse
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template

# --------------------------------------------------------------------------
# CONFIG. Change these two blocks if your column names differ.
# Run with --inspect to print the real table and column names.
# --------------------------------------------------------------------------

DB_PATH = os.environ.get("SALES_DB", "/data/sales.db")

TABLE = "sales"
COL = {
    "date": "date",          # TEXT, ISO 'YYYY-MM-DD'
    "amount": "amount",      # REAL, gross sale price
    "profit": "amount * 49.0 / 69.0",  # computed: $49 margin on $69 retail
    "state": "location",     # TEXT, full state name (stored as 'location')
    "platform": "platform",  # TEXT, Amazon / Walmart / eBay
}

# Rows whose state is one of these are counted in totals but excluded
# from anything geographic.
UNKNOWN_STATE = {"notspecified", "not specified", "unknown", "", None}

CACHE_SECONDS = 300

# --------------------------------------------------------------------------
# Reference data: Census Vintage 2025 resident population, July 1 2025.
# --------------------------------------------------------------------------

POP = {
    "California": 39355309, "Texas": 31709821, "Florida": 23462518,
    "New York": 20002427, "Pennsylvania": 13059432, "Illinois": 12719141,
    "Ohio": 11900510, "Georgia": 11302748, "North Carolina": 11197968,
    "Michigan": 10127884, "New Jersey": 9548215, "Virginia": 8880107,
    "Washington": 8001020, "Arizona": 7623818, "Tennessee": 7315076,
    "Massachusetts": 7154084, "Indiana": 6973333, "Missouri": 6270541,
    "Maryland": 6265347, "Colorado": 6012561, "Wisconsin": 5972787,
    "Minnesota": 5830405, "South Carolina": 5570274, "Alabama": 5193088,
    "Louisiana": 4618189, "Kentucky": 4606864, "Oregon": 4273586,
    "Oklahoma": 4123288, "Connecticut": 3688496, "Utah": 3538904,
    "Nevada": 3282188, "Iowa": 3238387, "Arkansas": 3114791,
    "Kansas": 2977220, "Mississippi": 2954160, "New Mexico": 2125498,
    "Idaho": 2029733, "Nebraska": 2018006, "West Virginia": 1766147,
    "Hawaii": 1432820, "New Hampshire": 1415342, "Maine": 1414874,
    "Montana": 1144694, "Rhode Island": 1114521, "Delaware": 1059952,
    "South Dakota": 935094, "North Dakota": 799358, "Alaska": 737270,
    "Vermont": 644663, "Wyoming": 588753,
}

ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}

REGION = {
    "West": ["Alaska", "Arizona", "California", "Colorado", "Hawaii", "Idaho",
             "Montana", "Nevada", "New Mexico", "Oregon", "Utah",
             "Washington", "Wyoming"],
    "Midwest": ["Illinois", "Indiana", "Iowa", "Kansas", "Michigan",
                "Minnesota", "Missouri", "Nebraska", "North Dakota", "Ohio",
                "South Dakota", "Wisconsin"],
    "South": ["Alabama", "Arkansas", "Delaware", "Florida", "Georgia",
              "Kentucky", "Louisiana", "Maryland", "Mississippi",
              "North Carolina", "Oklahoma", "South Carolina", "Tennessee",
              "Texas", "Virginia", "West Virginia"],
    "Northeast": ["Connecticut", "Maine", "Massachusetts", "New Hampshire",
                  "New Jersey", "New York", "Pennsylvania", "Rhode Island",
                  "Vermont"],
}
STATE_REGION = {s: r for r, ss in REGION.items() for s in ss}

# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def inspect_db(db_path=None):
    """Print every table and column so you can fill in TABLE and COL."""
    con = sqlite3.connect(db_path or DB_PATH)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not tables:
            print("No tables found in", db_path or DB_PATH)
        for t in tables:
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            print("%-24s %6d rows   %s" % (t, n, ", ".join(cols)))
        return tables
    finally:
        con.close()


def load_orders(db_path=None):
    """Read every sale as a plain dict. One row equals one unit."""
    con = sqlite3.connect(db_path or DB_PATH)
    con.row_factory = sqlite3.Row
    sql = (
        "SELECT {date} AS d, {amount} AS amount, {profit} AS profit, "
        "{state} AS state, {platform} AS platform FROM {table} "
        "WHERE {date} IS NOT NULL AND {date} != '' ORDER BY {date}"
    ).format(table=TABLE, **COL)
    try:
        rows = con.execute(sql).fetchall()
    finally:
        con.close()

    out = []
    for r in rows:
        d = _parse_date(r["d"])
        if d is None:
            continue
        state = (r["state"] or "").strip()
        out.append({
            "date": d,
            "amount": float(r["amount"] or 0),
            "profit": float(r["profit"] or 0),
            "state": state,
            "platform": (r["platform"] or "Unknown").strip(),
            "known_state": state.lower() not in UNKNOWN_STATE and state in POP,
        })
    out.sort(key=lambda o: o["date"])
    return out


def _parse_date(v):
    if isinstance(v, date):
        return v
    s = str(v).strip()[:19]
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------
# Small stats helpers, no scipy
# --------------------------------------------------------------------------

def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0.0
    m = len(xs) // 2
    return float(xs[m]) if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def _pct(a, b):
    return round(a / b * 100, 1) if b else 0.0


# --------------------------------------------------------------------------
# The breakdowns
# --------------------------------------------------------------------------

def compute(orders):
    """Everything the panel needs, as one JSON-safe dict."""
    if not orders:
        return {"empty": True}

    first, last = orders[0]["date"], orders[-1]["date"]
    span = (last - first).days + 1
    rev = sum(o["amount"] for o in orders)
    prof = sum(o["profit"] for o in orders)

    return {
        "empty": False,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "headline": {
            "units": len(orders),
            "revenue": round(rev, 2),
            "profit": round(prof, 2),
            "cogs": round(rev - prof, 2),
            "margin": round(prof / rev * 100, 1) if rev else 0,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "span_days": span,
        },
        "cadence": _cadence(orders, first, last, span),
        "monthly": _monthly(orders),
        "pace": _pace(orders, last),
        "geo": _geo(orders),
        "platform": _platform(orders),
        "stacks": _stacks(orders),
        "discovery": _discovery(orders),
        "price_points": _price_points(orders),
        "daily": _daily(orders, first, last),
    }


def _cadence(orders, first, last, span):
    """Gap between selling days. The cleanest growth signal in the file."""
    days = sorted({o["date"] for o in orders})
    gaps = [(days[i] - days[i - 1]).days for i in range(1, len(days))]
    half = len(gaps) // 2
    droughts = sorted(
        ({"ended": days[i].isoformat(), "days": gaps[i - 1]}
         for i in range(1, len(days))),
        key=lambda x: -x["days"])[:5]
    return {
        "selling_days": len(days),
        "coverage_pct": _pct(len(days), span),
        "mean_gap": round(sum(gaps) / len(gaps), 2) if gaps else 0,
        "median_gap": _median(gaps),
        "max_gap": max(gaps) if gaps else 0,
        "first_half_gap": round(sum(gaps[:half]) / half, 2) if half else 0,
        "second_half_gap": (round(sum(gaps[half:]) / len(gaps[half:]), 2)
                            if gaps[half:] else 0),
        "droughts": droughts,
    }


def _monthly(orders):
    buckets = defaultdict(lambda: {"units": 0, "revenue": 0.0, "profit": 0.0})
    for o in orders:
        b = buckets[o["date"].strftime("%Y-%m")]
        b["units"] += 1
        b["revenue"] += o["amount"]
        b["profit"] += o["profit"]

    out, cum_u, cum_r, prev = [], 0, 0.0, None
    for key in sorted(buckets):
        b = buckets[key]
        cum_u += b["units"]
        cum_r += b["revenue"]
        out.append({
            "month": key,
            "label": datetime.strptime(key, "%Y-%m").strftime("%b").upper(),
            "units": b["units"],
            "revenue": round(b["revenue"], 2),
            "profit": round(b["profit"], 2),
            "mom_pct": (round((b["units"] - prev) / prev * 100, 1)
                        if prev else None),
            "cum_units": cum_u,
            "cum_revenue": round(cum_r, 2),
        })
        prev = b["units"]
    return out


def _pace(orders, last):
    """Where the current month lands if today's rate holds."""
    key = last.strftime("%Y-%m")
    cur = [o for o in orders if o["date"].strftime("%Y-%m") == key]
    elapsed = last.day
    nxt = date(last.year + (last.month == 12), last.month % 12 + 1, 1)
    in_month = (nxt - date(last.year, last.month, 1)).days
    rate = len(cur) / elapsed if elapsed else 0
    return {
        "month": key,
        "units_so_far": len(cur),
        "days_elapsed": elapsed,
        "days_in_month": in_month,
        "per_day": round(rate, 3),
        "projected_units": round(rate * in_month, 1),
        "projected_revenue": round(
            rate * in_month * (sum(o["amount"] for o in cur) / len(cur)), 2
        ) if cur else 0,
    }


def _geo(orders):
    known = [o for o in orders if o["known_state"]]
    if not known:
        return {"states": [], "regions": [], "unknown": len(orders)}

    units = Counter(o["state"] for o in known)
    rev = defaultdict(float)
    for o in known:
        rev[o["state"]] += o["amount"]

    natl_rate = len(known) / (sum(POP.values()) / 1e6)
    states = []
    for s, n in units.items():
        per_m = n / (POP[s] / 1e6)
        states.append({
            "state": s, "abbr": ABBR[s], "units": n,
            "revenue": round(rev[s], 2),
            "per_million": round(per_m, 2),
            "_per_m_raw": per_m,          # rank on this, not the rounded value
            "index": round(per_m / natl_rate, 1),
            "region": STATE_REGION[s],
        })
    states.sort(key=lambda x: (-x["units"], x["state"]))
    for i, s in enumerate(states, 1):
        s["rank_units"] = i
    for i, s in enumerate(sorted(states, key=lambda x: -x["_per_m_raw"]), 1):
        s["rank_per_million"] = i
    for s in states:
        del s["_per_m_raw"]

    reg_units = Counter(o["state"] and STATE_REGION[o["state"]] for o in known)
    reg_pop = {r: sum(POP[s] for s in ss) for r, ss in REGION.items()}
    tot_pop = sum(reg_pop.values())
    regions = [{
        "region": r,
        "units": reg_units.get(r, 0),
        "unit_share_pct": _pct(reg_units.get(r, 0), len(known)),
        "pop_share_pct": _pct(reg_pop[r], tot_pop),
        "index": round((reg_units.get(r, 0) / len(known)) /
                       (reg_pop[r] / tot_pop), 2) if reg_units.get(r) else 0.0,
    } for r in ("West", "South", "Midwest", "Northeast")]

    shares = [n / len(known) for n in units.values()]
    return {
        "states": states,
        "regions": regions,
        "state_count": len(units),
        "national_per_million": round(natl_rate, 3),
        "hhi": round(sum(s * s for s in shares) * 10000),
        "unknown": len(orders) - len(known),
    }


def _platform(orders):
    c = Counter(o["platform"] for o in orders)
    n = len(orders)
    rows = [{"platform": p, "units": u, "share_pct": _pct(u, n)}
            for p, u in c.most_common()]
    return {
        "rows": rows,
        "hhi": round(sum((u / n) ** 2 for u in c.values()) * 10000),
    }


def _stacks(orders):
    """Same day, same state. Almost certainly one buyer taking several."""
    g = Counter((o["date"], o["state"]) for o in orders if o["known_state"])
    stacks = sorted(
        ({"date": d.isoformat(), "state": s, "units": n}
         for (d, s), n in g.items() if n > 1),
        key=lambda x: (-x["units"], x["date"]))
    in_stacks = sum(s["units"] for s in stacks)

    per_day = Counter(o["date"] for o in orders)
    return {
        "stacks": stacks,
        "units_in_stacks": in_stacks,
        "stacked_pct": _pct(in_stacks, len(orders)),
        "multi_order_days": sum(1 for v in per_day.values() if v > 1),
    }


def _discovery(orders):
    seen, rows = set(), []
    by_month = defaultdict(list)
    for o in orders:
        by_month[o["date"].strftime("%Y-%m")].append(o)

    repeat = 0
    for key in sorted(by_month):
        month_states = {o["state"] for o in by_month[key] if o["known_state"]}
        new = sorted(month_states - seen)
        seen |= month_states
        rows.append({"month": key, "units": len(by_month[key]),
                     "states": len(month_states), "new_states": len(new),
                     "new": new})

    seen2 = set()
    known_n = 0
    for o in orders:
        if not o["known_state"]:
            continue
        known_n += 1
        if o["state"] in seen2:
            repeat += 1
        seen2.add(o["state"])

    return {"rows": rows, "repeat_state_pct": _pct(repeat, known_n),
            "total_states": len(seen)}


def _price_points(orders):
    g = defaultdict(int)
    firsts = {}
    for o in orders:
        k = (round(o["amount"], 2), round(o["profit"], 2))
        g[k] += 1
        firsts.setdefault(k, o["date"])
    return sorted(
        ({"amount": a, "profit": p, "cost": round(a - p, 2),
          "margin": round(p / a * 100, 1) if a else 0,
          "units": n, "first_seen": firsts[(a, p)].isoformat()}
         for (a, p), n in g.items()),
        key=lambda x: x["first_seen"])


def _daily(orders, first, last):
    """One entry per calendar day, for the cut-line strip."""
    c = Counter(o["date"] for o in orders)
    out, d = [], first
    while d <= last:
        out.append(c.get(d, 0))
        d += timedelta(days=1)
    return {"start": first.isoformat(), "counts": out}


# --------------------------------------------------------------------------
# Flask wiring
# --------------------------------------------------------------------------

analytics_bp = Blueprint("analytics", __name__, template_folder="templates")

_cache = {"at": None, "data": None}


def get_analytics(force=False):
    now = datetime.now()
    if (not force and _cache["at"]
            and (now - _cache["at"]).total_seconds() < CACHE_SECONDS):
        return _cache["data"]
    db = current_app.config.get("SALES_DB", DB_PATH) if current_app else DB_PATH
    data = compute(load_orders(db))
    _cache.update(at=now, data=data)
    return data


@analytics_bp.route("/analytics")
def analytics_page():
    return render_template("analytics_panel.html", a=get_analytics())


@analytics_bp.route("/api/analytics.json")
def analytics_json():
    return jsonify(get_analytics())


# --------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--inspect", action="store_true",
                    help="print tables and columns, then exit")
    args = ap.parse_args()

    if args.inspect:
        inspect_db(args.db)
    else:
        print(json.dumps(compute(load_orders(args.db)), indent=2))
