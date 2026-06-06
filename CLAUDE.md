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

The Flask app runs as a single Docker container managed by `docker-compose.yml`, with a named volume (`db-data`) mounting the SQLite file at `/app/data/sales.db`. Port: 5050.

The Pi is reachable via Tailscale as `dataworks`. SSH user is `loyal`, project lives at `~/sandbag/sales-tracker/` (docker-compose.yml is in that subdirectory).

```bash
# Standard deploy (from project root):
./deploy.sh "your commit message"   # git add/commit/push + SSH pull + container rebuild

# First-time setup on the Pi:
cp .env.example .env && nano .env
docker compose up -d
docker compose logs -f
```

`deploy.sh` (project root) handles the full deploy in one command. It skips the commit step if there's nothing staged.

### Architecture

| File | Role |
|---|---|
| `app.py` | Flask routes: `/` dashboard, `/log` sale form, `/milestones`, `/sales/<id>/delete` + `/sales/<id>/edit` (POST), `/api/sales` + `/api/stats` (JSON) |
| `database.py` | All SQLite access. `db_init()` must run before first request. |
| `prediction.py` | Weather-aware next-sale prediction. Consumes `stats` + recent locations, calls NWS API (`api.weather.gov/alerts/active`) for severe-weather alerts in states we've sold to, combines with WMA-based frequency + seasonal baselines. Called by the `/` route; rendered inside the prediction card in `index.html`. |
| `templates/base.html` | Nav, `:root` design tokens, topographic backdrop, shared components (eyebrow, divider, kpi, badge, floodwater vessel, staggered reveal, reduced-motion guard). Self-hosts fonts + Chart.js from `static/` (see below). |
| `templates/index.html` | Dashboard: asymmetric hero (oversized total-revenue + floodwater milestone vessel), weather-forecast hero feature (prediction + multi-hazard merged), KPI cluster, 4 charts, recent-transactions table, full-screen "All Transactions" panel (sticky search + month-grouped rows with per-month sparklines + footer totals), delete-confirm modal |
| `templates/log_sale.html` | Sale entry form with location preset chips |
| `templates/milestones.html` | Milestones page; next-milestone shown as a floodwater vessel, then completed/upcoming lists from `get_milestones()` |
| `static/fonts/` | Self-hosted woff2 + `fonts.css` (Bricolage Grotesque display, Instrument Sans body) — no Google Fonts CDN, so the UI renders offline on the Pi |
| `static/js/` | Vendored `chart.umd.js` (4.4.0) + `chartjs-plugin-datalabels.min.js` (2.x) — no CDN |
| `mockups/dashboard.html` | Standalone static design PoC (sample data, not wired to Flask) |

**Dashboard charts** (`index.html`):
| Chart | Type | Data source |
|---|---|---|
| Platform Split | Horizontal bar (count) | `by_platform` |
| State Split | Horizontal bar (count) | `by_location` |
| By Month | Vertical bar + ChartDataLabels | `by_month` |
| Weekly Revenue | Line | `by_week` |

### `database.py` public API

| Function | Returns |
|---|---|
| `db_init()` | Creates table if not exists — call once at startup |
| `add_sale(date_str, amount, location, platform, notes)` | Inserts a row; normalizes platform via `_normalize_platform()` |
| `update_sale(sale_id, amount=None, notes=None)` | Updates amount and/or notes for an existing sale |
| `get_all_sales()` | `list[dict]` ordered by date DESC, id DESC. **Adds a computed `profit` field** (= `amount × 49/69`) to each row. |
| `get_stats()` | Stats dict (see below) |
| `get_distinct_locations()` | `list[str]` top-10 locations by use count, most-used first |
| `delete_sale(sale_id)` | Deletes a sale row by id |
| `get_milestones()` | Dict of `{milestones, total_hit, total_possible, milestone_velocity, next_milestone}`. Milestones are hardcoded in the function body; types: `count`, `revenue`, `platform`, `oos`, `states`, `monthly`. |

The `log_sale` GET route passes `today` (ISO date string) and `locations` (from `get_distinct_locations()`) to `log_sale.html`. Location values are `.title()`-cased on POST before being stored — chips and stored values will always be title-cased.

### Profit formula

Profit per sale is **`amount × 49/69`** (our unit cost is $20 on a $69 retail price → $49 margin, scaled linearly). This constant appears in three places — keep them in sync when the margin changes:
- `database.get_all_sales()` — server-side per-row `profit` field
- `database.get_stats()` — `PROFIT_MARGIN = 49 / 69` used for `total_profit`, `this_month_profit`
- `templates/index.html` JS amount-edit handler — `const profit = (val * 49 / 69);` recomputes profit when a cell is edited inline (so the profit column updates without a page reload)

### Data model

```
sales: id, date (TEXT ISO-8601), amount (REAL), location (TEXT),
       platform (TEXT: "Amazon"|"eBay"|"Walmart"), notes (TEXT), created_at
```

`database.py` normalizes platform strings via `_normalize_platform()` before insert.

### Stats object (`get_stats()` return value)

```python
{
  "total_revenue": float,
  "total_sales": int,
  "total_profit": float,                    # total_revenue × 49/69
  "this_month_revenue": float,
  "this_month_sales": int,
  "this_month_profit": float,
  "projected_month_revenue": float,         # daily avg × days_in_month (end-of-month projection)
  "avg_sale": float,
  "by_month": [{"month": "YYYY-MM", "revenue": float, "count": int}, ...],
  "by_platform": [{"platform": str, "count": int, "revenue": float}, ...],  # sorted by revenue DESC
  "by_location": [{"location": str, "revenue": float, "count": int}, ...],  # top 10 by revenue
  "by_week": [{"week": "D Mon", "revenue": float, "count": int}, ...],       # every Mon from first sale to today, zero-filled for weeks with no sales
  "last_month_revenue": float,
  "last_month_sales": int,
  "avg_weekly_revenue": float,
  "avg_weekly_sales": float,
  "avg_days_between_sales": float | None,   # None if < 2 sales
  "longest_streak": int,                    # longest run of consecutive sale days
  "last_sale_date": "YYYY-MM-DD" | None,
  "days_since_last_sale": int | None,
  "sale_gaps": list[int],                   # gap in days between each consecutive sale date (feeds prediction WMA)
  "rolling_30_revenue": float,              # revenue in trailing 30 days (today included)
  "rolling_30_count": int,                  # sale count in trailing 30 days
  "rolling_30_delta_pct": float | None,     # % change vs the prior 30-day window; None if no prior data
  "rolling_30_daily": list[float],          # length-30 daily revenue series (oldest first), zero-filled
  "prev_30_daily": list[float],             # length-30 daily revenue for the prior window (days 30-59 ago), oldest first, zero-filled
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

**"Floodworks" design system.** All colors + fonts are CSS custom properties in `base.html`'s `:root` — change them there, not per-page. Tokens: surfaces `--bg #0a0f15` / `--card #121c28` / `--border #223141`; story palette `--hazard` orange (alerts), `--hazard-2` yellow, `--water`/`--water-2`/`--water-deep` floodwater teals, `--kraft` sandbag tan, `--good` green, `--bad` rose; fonts `--font-display` (Bricolage Grotesque, headings/big numbers) + `--font-body` (Instrument Sans). Signature element: the **floodwater vessel** (`.vessel` + `.water`, in base.html) — an animated rising-water fill used for the dashboard milestone and the milestones-page hero. A faint topographic SVG backdrop (`.topo`) sits behind every page. The kraft→orange→yellow gradient (`.hero-num` / `.form-title`) is the recurring hero-text treatment. Staggered `.reveal` entrance + count-ups on load; all motion is wrapped in a `prefers-reduced-motion` guard.

Transaction-row conventions (both dashboard preview and full-screen panel):
- Platform **badge** classes (in base.html): `.b-amazon` green, `.b-ebay` cyan, `.b-walmart` yellow; `.b-new` for the NEW pill.
- Platform-colored **left-border accent** (3px `box-shadow: inset 3px 0 0 …` on first `<td>`): Amazon `var(--good)`, eBay `var(--water-2)`, Walmart `var(--hazard-2)`. These are selected via `tr[data-platform="…"]` — every transaction row must have `data-platform="{{ s.platform }}"`.
- "NEW" pulse badge on the most recent row + an orange gradient tint via `tr.row-highlight`.
- The full-screen panel groups rows by month using a Jinja `namespace` to emit `<tr class="month-header" data-month="YYYY-MM">` before the first row of each new month. JS (`enrichTxPanel()`) fills in the label/subtotal/count and draws a per-month orange sparkline onto the inline `<canvas class="mini-spark">` when the panel is first opened. Sparkline rendering is deferred to panel-open because `canvas.clientWidth` is 0 while `display:none`.
- Relative dates ("Today" / "Yesterday" / "Nd ago" within 7 days) are applied client-side to any `<span class="rel-date" data-date="…">` — the raw ISO date goes in `data-date`, server-rendered text is the fallback.

## Asset Folder Conventions

| Folder | Contents |
|---|---|
| `01_raw-media/` | Original unedited photos/videos from phone |
| `02_product-assets/` | Final product photos, graphics, illustrations |
| `03_compressed/` | Resized/compressed exports (1:1 sets for Amazon/eBay) |
| `04_videos/` | Edited video exports |
| `05_insert-paper/` | Product insert / packaging documents |
| `06_documents/` | Quick-use guide PDFs, QR code, feedback forms |
