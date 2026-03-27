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
