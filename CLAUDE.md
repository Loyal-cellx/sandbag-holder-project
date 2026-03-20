# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sandbag holder product business sold on Amazon and eBay (through grandpa's accounts). Grandpa forwards invoice copies when sales occur. This repo holds product media assets and a self-hosted sales tracking web app.

## Sales Tracker App (`sales-tracker/`)

Flask + SQLite web app deployed on a **Raspberry Pi 5** (also running Jellyfin on 8096 and Immich on 2283). Accessed privately via **Tailscale** on the owner's phone. Sales are logged manually via a web form when an invoice arrives.

### Running locally

```bash
cd sales-tracker
py -m venv venv                  # Windows: use `py`, not `python`
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in SECRET_KEY
py app.py                        # http://localhost:5050
```

There are no automated tests or linting configured for this project.

To re-seed historical sales data (not committed — one-time use):
```bash
py seed.py   # create this as a throwaway script using database.add_sale()
```

### Deployment on the Pi (Docker)

The app runs as two Docker containers managed by `docker-compose.yml`:

| Service | Port | Purpose |
|---|---|---|
| `sales-tracker` | 5050 | Flask app (built from `Dockerfile`) |
| `db-editor` | 5051 | sqlite-web — browse/edit raw rows |

Both share a named Docker volume (`db-data`) so they access the same `sales.db`.

```bash
# Copy to Pi, SSH in, then:
cp .env.example .env && nano .env
docker compose up -d
docker compose logs -f
```

### Architecture

| File | Role |
|---|---|
| `app.py` | Flask routes: `/` dashboard, `/log` sale form, `/api/sales` + `/api/stats` (JSON, for headless access) |
| `database.py` | All SQLite access. `db_init()` must run before first request. |
| `templates/base.html` | Nav, global CSS, Chart.js CDN import |
| `templates/index.html` | Dashboard: KPI cards, Platform donut + By Month bar charts, transactions table |
| `templates/log_sale.html` | Sale entry form with location preset chips |

### `database.py` public API

| Function | Returns |
|---|---|
| `db_init()` | Creates table if not exists — call once at startup |
| `add_sale(date_str, amount, location, platform, notes)` | Inserts a row; normalizes platform via `_normalize_platform()` |
| `get_all_sales()` | `list[dict]` ordered by date DESC, id DESC |
| `get_stats()` | Stats dict (see below) |
| `get_distinct_locations()` | `list[str]` top-10 locations by use count, most-used first |

The `log_sale` GET route passes `today` (ISO date string) and `locations` (from `get_distinct_locations()`) to `log_sale.html`. Location values are `.title()`-cased on POST before being stored — chips and stored values will always be title-cased.

### Data model

```
sales: id, date (TEXT ISO-8601), amount (REAL), location (TEXT),
       platform (TEXT: "Amazon"|"eBay"), notes (TEXT), created_at
```

`database.py` normalizes platform strings via `_normalize_platform()` before insert.

### Stats object (`get_stats()` return value)

```python
{
  "total_revenue": float,
  "total_sales": int,
  "this_month_revenue": float,
  "this_month_sales": int,
  "avg_sale": float,
  "by_month": [{"month": "YYYY-MM", "revenue": float, "count": int}, ...],
  "by_platform": [{"platform": str, "count": int, "revenue": float}, ...],  # sorted by revenue DESC
  "by_location": [{"location": str, "revenue": float, "count": int}, ...],  # top 10 by revenue
  "by_week": [{"week": "D Mon", "revenue": float, "count": int}, ...]        # every Mon from first sale to today, zero-filled for weeks with no sales
}
```

`index.html` uses `by_platform[0]` and `by_location[0]` for the Top Platform / Top State KPI cards — guard with `{% if stats.by_platform %}` before indexing. `by_week` always covers every Monday from the first sale date to today; missing weeks are injected with `revenue: 0, count: 0` in Python (not in SQL).

### Environment variables (`.env`)

```
SECRET_KEY=           # Flask session secret
PORT=5050
DB_PATH=              # optional: override SQLite file path (Docker sets this to /app/data/sales.db)
```

### UI design notes

Dashboard matches `examplesales_summary.html` (project root): dark theme (`#0f1117` bg, `#1e2130` cards), orange/blue/green/purple KPI card top-borders, orange bar chart, alternating row highlight on transactions table, "NEW" badge on most recent row. Amazon badge = green, eBay badge = blue.

## Asset Folder Conventions

| Folder | Contents |
|---|---|
| `01_raw-media/` | Original unedited photos/videos from phone |
| `02_product-assets/` | Final product photos, graphics, illustrations |
| `03_compressed/` | Resized/compressed exports (1:1 sets for Amazon/eBay) |
| `04_videos/` | Edited video exports |
| `05_insert-paper/` | Product insert / packaging documents |
| `06_documents/` | Quick-use guide PDFs, QR code, feedback forms |
