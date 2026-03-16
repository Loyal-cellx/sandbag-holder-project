import sqlite3
from datetime import datetime, date, timedelta
import datetime as dt
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
    return platform.strip()


def add_sale(date_str, amount, location, platform, notes=""):
    conn = _connect()
    conn.execute(
        "INSERT INTO sales (date, amount, location, platform, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (date_str, amount, location, _normalize_platform(platform), notes, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def get_all_sales():
    conn = _connect()
    rows = conn.execute("SELECT * FROM sales ORDER BY date DESC, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


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

    conn.close()

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
    }
