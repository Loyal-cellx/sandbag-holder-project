import sqlite3
from datetime import datetime, date, timedelta
import datetime as dt
import calendar
import os

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "sales.db"))


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            location TEXT NOT NULL,
            platform TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _normalize_platform(platform):
    p = platform.strip().lower()
    if p == "amazon":
        return "Amazon"
    if p in ("ebay", "e-bay"):
        return "eBay"
    if p == "walmart":
        return "Walmart"
    return platform.strip()


def add_sale(date_str, amount, location, platform, notes=""):
    conn = _connect()
    conn.execute(
        "INSERT INTO sales (date, amount, location, platform, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (date_str, amount, location, _normalize_platform(platform), notes, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def delete_sale(sale_id):
    conn = _connect()
    conn.execute("DELETE FROM sales WHERE id = ?", (sale_id,))
    conn.commit()
    conn.close()


def get_all_sales():
    conn = _connect()
    rows = conn.execute("SELECT * FROM sales ORDER BY date DESC, id DESC").fetchall()
    conn.close()
    return [dict(r) | {"profit": round(r["amount"] * 49 / 69, 2)} for r in rows]


def get_distinct_locations():
    conn = _connect()
    rows = conn.execute("""
        SELECT location, COUNT(*) AS cnt
        FROM sales
        GROUP BY location
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    conn.close()
    return [r["location"] for r in rows]


def get_stats():
    conn = _connect()

    total_revenue = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM sales").fetchone()[0]
    total_sales = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]

    this_month = date.today().strftime("%Y-%m")
    this_month_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM sales WHERE strftime('%Y-%m', date) = ?", (this_month,)
    ).fetchone()[0]
    this_month_sales = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE strftime('%Y-%m', date) = ?", (this_month,)
    ).fetchone()[0]

    avg_sale = (total_revenue / total_sales) if total_sales else 0

    by_month_rows = conn.execute("""
        SELECT strftime('%Y-%m', date) AS month,
               SUM(amount) AS revenue,
               COUNT(*) AS count
        FROM sales
        GROUP BY month
        ORDER BY month
    """).fetchall()
    by_month = [{"month": r["month"], "revenue": round(r["revenue"], 2), "count": r["count"]} for r in by_month_rows]

    platform_rows = conn.execute("""
        SELECT platform, COUNT(*) AS count, SUM(amount) AS revenue
        FROM sales
        GROUP BY platform
        ORDER BY revenue DESC
    """).fetchall()
    by_platform = [{"platform": r["platform"], "count": r["count"], "revenue": round(r["revenue"], 2)} for r in platform_rows]

    location_rows = conn.execute("""
        SELECT location, SUM(amount) AS revenue, COUNT(*) AS count
        FROM sales
        GROUP BY location
        ORDER BY revenue DESC
        LIMIT 10
    """).fetchall()
    by_location = [{"location": r["location"], "revenue": round(r["revenue"], 2), "count": r["count"]} for r in location_rows]

    week_rows = conn.execute("""
        SELECT strftime('%Y-%W', date) AS week,
               SUM(amount) AS revenue,
               COUNT(*)    AS count
        FROM sales
        GROUP BY week
        ORDER BY week
    """).fetchall()

    last_month_str = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    last_month_revenue = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM sales WHERE strftime('%Y-%m', date) = ?",
        (last_month_str,)
    ).fetchone()[0]
    last_month_sales = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE strftime('%Y-%m', date) = ?",
        (last_month_str,)
    ).fetchone()[0]

    week_data = {}
    first_monday = None
    for r in week_rows:
        monday = dt.datetime.strptime(r["week"] + "-1", "%Y-%W-%w").date()
        week_data[monday] = {"revenue": round(r["revenue"], 2), "count": r["count"]}
        if first_monday is None or monday < first_monday:
            first_monday = monday

    by_week = []
    if first_monday:
        today_monday = date.today() - timedelta(days=date.today().weekday())
        cur = first_monday
        while cur <= today_monday:
            d = week_data.get(cur, {"revenue": 0.0, "count": 0})
            by_week.append({
                "week": cur.strftime("%d %b").lstrip("0"),
                "revenue": d["revenue"],
                "count": d["count"],
            })
            cur += timedelta(weeks=1)

    weeks_elapsed = max(len(by_week), 1)
    avg_weekly_revenue = round(total_revenue / weeks_elapsed, 2)
    avg_weekly_sales   = round(total_sales   / weeks_elapsed, 1)

    PROFIT_MARGIN = 49 / 69
    total_profit = round(total_revenue * PROFIT_MARGIN, 2)
    this_month_profit = round(this_month_revenue * PROFIT_MARGIN, 2)

    today_obj = date.today()
    days_elapsed = today_obj.day
    days_in_month = calendar.monthrange(today_obj.year, today_obj.month)[1]
    daily_avg = this_month_revenue / days_elapsed if days_elapsed > 0 else 0
    projected_month_revenue = round(daily_avg * days_in_month, 2)

    all_dates_rows = conn.execute(
        "SELECT DISTINCT date FROM sales ORDER BY date ASC"
    ).fetchall()
    conn.close()
    sale_dates = [datetime.strptime(r["date"], "%Y-%m-%d").date() for r in all_dates_rows]
    if len(sale_dates) >= 2:
        gaps = [(sale_dates[i+1] - sale_dates[i]).days for i in range(len(sale_dates)-1)]
        avg_days_between_sales = round(sum(gaps) / len(gaps), 1)
    else:
        avg_days_between_sales = None

    longest_streak = 0
    if sale_dates:
        cur_streak = 1
        for i in range(1, len(sale_dates)):
            if (sale_dates[i] - sale_dates[i-1]).days == 1:
                cur_streak += 1
                longest_streak = max(longest_streak, cur_streak)
            else:
                cur_streak = 1
        longest_streak = max(longest_streak, 1)

    return {
        "total_revenue": round(total_revenue, 2),
        "total_sales": total_sales,
        "this_month_revenue": round(this_month_revenue, 2),
        "this_month_sales": this_month_sales,
        "avg_sale": round(avg_sale, 2),
        "by_month": by_month,
        "by_platform": by_platform,
        "by_location": by_location,
        "by_week": by_week,
        "last_month_revenue": round(last_month_revenue, 2),
        "last_month_sales": last_month_sales,
        "avg_weekly_revenue": avg_weekly_revenue,
        "avg_weekly_sales": avg_weekly_sales,
        "total_profit": total_profit,
        "this_month_profit": this_month_profit,
        "projected_month_revenue": projected_month_revenue,
        "avg_days_between_sales": avg_days_between_sales,
        "longest_streak": longest_streak,
    }


def get_milestones():
    MILESTONES = [
        {"slug": "first_sale",    "name": "First Sale",                  "type": "count",    "threshold": 1},
        {"slug": "first_ebay",    "name": "First eBay Sale",             "type": "platform", "threshold": "eBay"},
        {"slug": "first_walmart", "name": "First Walmart Sale",          "type": "platform", "threshold": "Walmart"},
        {"slug": "first_oos",     "name": "First Out-of-State Sale",     "type": "oos",      "threshold": None},
        {"slug": "states_5",      "name": "Sell in 5 Different States",  "type": "states",   "threshold": 5},
        {"slug": "rev_500",       "name": "$500 Revenue",                "type": "revenue",  "threshold": 500},
        {"slug": "rev_1000",      "name": "$1,000 Revenue",              "type": "revenue",  "threshold": 1000},
        {"slug": "sales_25",      "name": "25 Sales",                    "type": "count",    "threshold": 25},
        {"slug": "month_500",     "name": "$500 in a Single Month",      "type": "monthly",  "threshold": 500},
        {"slug": "rev_1500",      "name": "$1,500 Revenue",              "type": "revenue",  "threshold": 1500},
        {"slug": "rev_2000",      "name": "$2,000 Revenue",              "type": "revenue",  "threshold": 2000},
        {"slug": "states_10",     "name": "Sell in 10 Different States", "type": "states",   "threshold": 10},
        {"slug": "sales_50",      "name": "50 Sales",                    "type": "count",    "threshold": 50},
        {"slug": "month_1000",    "name": "$1,000 in a Single Month",    "type": "monthly",  "threshold": 1000},
        {"slug": "rev_2500",      "name": "$2,500 Revenue",              "type": "revenue",  "threshold": 2500},
        {"slug": "sales_100",     "name": "100 Sales",                   "type": "count",    "threshold": 100},
    ]

    conn = _connect()
    rows = conn.execute(
        "SELECT id, date, amount, location, platform FROM sales ORDER BY date ASC, id ASC"
    ).fetchall()
    conn.close()

    running_revenue = 0.0
    running_count = 0
    first_location = None
    seen_locations = set()
    seen_platforms = set()
    running_monthly = {}
    hit_dates = {}  # slug -> ISO date string
    hit_set = set()

    for row in rows:
        running_revenue += row["amount"]
        running_count += 1
        if first_location is None:
            first_location = row["location"]
        seen_locations.add(row["location"])
        seen_platforms.add(row["platform"])
        month_key = row["date"][:7]
        running_monthly[month_key] = running_monthly.get(month_key, 0) + row["amount"]

        for m in MILESTONES:
            slug = m["slug"]
            if slug in hit_set:
                continue
            t = m["type"]
            thresh = m["threshold"]
            hit = False
            if t == "count":
                hit = running_count >= thresh
            elif t == "revenue":
                hit = running_revenue >= thresh
            elif t == "platform":
                hit = row["platform"] == thresh
            elif t == "oos":
                hit = first_location is not None and row["location"] != first_location
            elif t == "states":
                hit = len(seen_locations) >= thresh
            elif t == "monthly":
                hit = running_monthly.get(month_key, 0) >= thresh
            if hit:
                hit_dates[slug] = row["date"]
                hit_set.add(slug)

    # Current progress for unhit milestones
    max_monthly = max(running_monthly.values(), default=0.0)

    def current_val(m):
        t = m["type"]
        if t == "count":
            return running_count
        if t == "revenue":
            return running_revenue
        if t == "states":
            return len(seen_locations)
        if t == "monthly":
            return max_monthly
        return 0  # platform, oos — binary

    def gap_display(m, current):
        t = m["type"]
        thresh = m["threshold"]
        if t == "revenue":
            return f"${thresh - current:,.0f} to go"
        if t == "count":
            return f"{thresh - current} more sales"
        if t == "states":
            return f"{thresh - current} more states"
        if t == "monthly":
            return f"${thresh - current:,.0f} more in one month"
        return "Not yet"

    result = []
    for m in MILESTONES:
        slug = m["slug"]
        hit = slug in hit_dates
        cur = current_val(m) if not hit else m["threshold"]
        thresh = m["threshold"]

        if hit:
            pct = 100.0
            hit_date_str = hit_dates[slug]
            hit_date_display = datetime.strptime(hit_date_str, "%Y-%m-%d").strftime("%b %d, %Y")
            gap = None
        else:
            thresh_num = thresh if isinstance(thresh, (int, float)) else 1
            cur_num = cur if isinstance(cur, (int, float)) else 0
            if thresh_num > 0 and isinstance(thresh, (int, float)):
                pct = min(99.9, cur_num / thresh_num * 100)
            else:
                pct = 0.0
            hit_date_display = None
            hit_date_str = None
            gap = gap_display(m, cur)

        result.append({
            "slug": slug,
            "name": m["name"],
            "type": m["type"],
            "threshold": thresh,
            "hit": hit,
            "hit_date": hit_date_str,
            "hit_date_display": hit_date_display,
            "progress_pct": round(pct, 1),
            "current": cur,
            "gap_display": gap,
            "days_since_prev": None,
            "is_newest": False,
        })

    # days_since_prev and is_newest on hit milestones
    hit_results = sorted(
        [r for r in result if r["hit"]],
        key=lambda r: r["hit_date"]
    )
    for i, r in enumerate(hit_results):
        if i == 0:
            r["days_since_prev"] = None
        else:
            prev_date = datetime.strptime(hit_results[i - 1]["hit_date"], "%Y-%m-%d").date()
            this_date = datetime.strptime(r["hit_date"], "%Y-%m-%d").date()
            r["days_since_prev"] = (this_date - prev_date).days
    if hit_results:
        hit_results[-1]["is_newest"] = True

    total_hit = len(hit_results)
    total_possible = len(MILESTONES)

    # milestone_velocity
    milestone_velocity = None
    if hit_results:
        first_hit_date = datetime.strptime(hit_results[0]["hit_date"], "%Y-%m-%d").date()
        months_elapsed = (date.today() - first_hit_date).days / 30.44
        if months_elapsed > 0:
            milestone_velocity = round(total_hit / months_elapsed, 2)

    # next_milestone: unhit with highest progress_pct
    unhit = [r for r in result if not r["hit"]]
    next_milestone = max(unhit, key=lambda r: r["progress_pct"]) if unhit else None

    return {
        "milestones": result,
        "total_hit": total_hit,
        "total_possible": total_possible,
        "milestone_velocity": milestone_velocity,
        "next_milestone": next_milestone,
    }
