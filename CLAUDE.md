# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Sandbag holder product business sold on Amazon and eBay (through grandpa's accounts). Grandpa forwards invoice copies when sales occur. This repo holds product media assets and a self-hosted sales tracking web app.

**Repo gotcha:** `.gitignore` ends with an Obsidian-vault block that ignores `*.md` wholesale, with narrow exceptions (`!CLAUDE.md`, `!README.md`, `!sales-tracker/**/*.md`). Any new markdown doc written elsewhere in the tree is silently untracked — `git add` it explicitly or it will look committed when it isn't.

## Sales Tracker App (`sales-tracker/`)

Flask + SQLite web app accessed privately via **Tailscale** on the owner's phone. Sales are logged manually via a web form when an invoice arrives.

**Where it runs (verified 2026-08-15):** production is now `amboss` (the x86 home server), not the Raspberry Pi. The app was migrated off `dataworks` on 2026-08-14 and the Pi's container is stopped. Repo lives at `/home/loyal/sandbag` on amboss; compose file is in `sales-tracker/`.

⚠️ **`.deploy.env` still points at the old host.** It contains `DEPLOY_HOST=loyal@dataworks`, so running `./deploy.sh` deploys to the *stopped Pi instance*, not the live one. Fix that file before using the deploy script.

### Running locally

```bash
cd sales-tracker
py -m venv venv                  # Windows: use `py`, not `python`
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in SECRET_KEY
py app.py                        # http://localhost:5050
```

### Tests

There are **22 pytest tests** in `sales-tracker/tests/` (`test_app.py`, `test_database.py`). `pytest` is deliberately *not* in `requirements.txt` — the production image ships without it, so install it separately to run the suite.

```bash
cd sales-tracker
pip install pytest
python -m pytest tests -q                       # whole suite
python -m pytest tests/test_app.py -q           # one file
python -m pytest tests/test_app.py::test_api_prediction_keys -q   # single test
```

To run them without touching your environment, use the built image:
```bash
docker run --rm -v "$PWD":/src -w /src sales-tracker-sales-tracker \
  sh -c "pip install -q pytest && python -m pytest tests -q"
```

`tests/conftest.py` provides a `db` fixture (points `database.DB_PATH` at a throwaway SQLite file via monkeypatch) and a `client` fixture layered on it. Every DB function resolves `DB_PATH` at call time, which is what makes that patching work for both direct DB tests and Flask routes.

All 22 pass as of 2026-08-19 (the long-failing `test_api_prediction_keys` was fixed along with the empty-`state_codes` bug — see below).

To re-seed historical sales data (not committed — one-time use):
```bash
py seed.py   # create this as a throwaway script using database.add_sale()
```

### Deployment (Docker)

The Flask app runs as a single Docker container managed by `docker-compose.yml`, with a named volume (`db-data`) mounting the SQLite file at `/app/data/sales.db`. Port: 5050.

**Production server is gunicorn, not `app.py`.** The Dockerfile ends with:
```
gunicorn --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:5050 app:app
```
One worker, four threads. The `--timeout 60` matters — see the cold-start trap below, where a first request has been measured at 59.2s, right at the edge of the worker being killed.

```bash
# Standard deploy (from project root) — CHECK .deploy.env FIRST, see warning above:
./deploy.sh "your commit message"   # git add/commit/push + SSH pull + container rebuild

# On the host directly:
docker compose up -d
docker compose logs -f
```

`deploy.sh` reads `DEPLOY_HOST` / `DEPLOY_PATH` from a gitignored `.deploy.env` and skips the commit step if nothing is staged. It runs `docker compose down && up -d --build`, which **wipes the in-process caches** — the next page load pays full cold-start cost (see below).

**Port binding convention on amboss.** Docker writes its rules into the `nat` PREROUTING chain, which is evaluated before the INPUT chain ufw filters on — so a published port is reachable regardless of ufw. Exposure is controlled by the host IP in the `ports:` line, not by firewall rules:

```yaml
ports:
  - "${TS_IP}:5050:5050"              # tailnet
  - "${LAN_IP:-127.0.0.1}:5050:5050"  # LAN
```

Both are set in `sales-tracker/.env`. `LAN_IP` carries a `:-127.0.0.1` default so an unset variable fails closed — an empty host IP would make Docker bind `0.0.0.0` and publish to everything.

**Hostname gotcha:** the bare name `amboss` has two answers — the router resolves it to the LAN IP from its DHCP reservation, MagicDNS resolves it to the tailnet IP. Tailscale's split-DNS route only covers `ts.net.`, so a single-label `amboss` is not automatically a MagicDNS query. Use the FQDN `amboss.tailc10443.ts.net:5050` when you need it unambiguous.

### Architecture

| File | Role |
|---|---|
| `app.py` | Flask routes — pages: `/` dashboard, `/log` sale form, `/milestones`, `/history`. Fragments: `/partial/forecast` (server-rendered forecast section, fetched async by the dashboard). Mutations: `/sales/<id>/delete`, `/sales/<id>/edit` (POST). JSON: `/api/sales`, `/api/stats`, `/api/prediction`, `/api/ai-forecast`, `/api/sale-weather[/<id>]`, `/api/snapshots`, `/api/take-snapshot` (POST). Ops: `/health` (used by the Docker HEALTHCHECK). |
| `database.py` | All SQLite access. `db_init()` must run before first request. Resolves `DB_PATH` at call time, not import time — this is what lets tests monkeypatch it. |
| `prediction.py` | Weather-aware next-sale prediction. Fans **5 external feeds** out concurrently (`ThreadPoolExecutor(max_workers=5)`): NWS alerts, NIFC active fires, NIFC burn scars, NHC tropical cyclones, USGS flood gauges. Combines them with WMA-based frequency + seasonal baselines into a 0-99 score. Called by `/partial/forecast` (fetched async from the dashboard), `/api/prediction`, and `/api/ai-forecast` — **not** by `/` since 2026-08-19. **Read the cold-start trap below before touching this file.** |
| `tuftler_analytics.py` | Flask **blueprint** (`analytics_bp`), registered in `app.py:20`. Adds `/analytics` (rendered Jinja panel) and `/api/analytics.json`. Stdlib only — computes the July 2026 teardown breakdowns straight off the SQLite DB. Its routes won't show up in an `@app.route` grep of `app.py`. |
| `snapshot.py` | Daily climate + sales snapshot collector. CLI: `python snapshot.py` (today) or `--date YYYY-MM-DD` (backfill). Idempotent — rerunning a date overwrites it. Feeds `/api/snapshots`. |
| `backfill_weather.py` | One-off backfill of historical weather for sales missing it, via the Open-Meteo archive API (no key). Maps each sale's state to a capital-centroid lat/lon. Flags: `--all` (re-fetch everything), `--dry-run`. |
| `templates/base.html` | Nav, `:root` design tokens, topographic backdrop, shared components (eyebrow, divider, kpi, badge, floodwater vessel, staggered reveal, reduced-motion guard). Self-hosts fonts + Chart.js from `static/` (see below). |
| `templates/index.html` | Dashboard: asymmetric hero (oversized total-revenue + floodwater milestone vessel), weather-forecast hero feature (prediction + multi-hazard merged), KPI cluster, 4 charts, recent-transactions table, full-screen "All Transactions" panel (sticky search + month-grouped rows with per-month sparklines + footer totals), delete-confirm modal |
| `templates/log_sale.html` | Sale entry form with location preset chips |
| `templates/milestones.html` | Milestones page; next-milestone shown as a floodwater vessel, then completed/upcoming lists from `get_milestones()` |
| `static/fonts/` | Self-hosted woff2 + `fonts.css` (Bricolage Grotesque display, Instrument Sans body) — no Google Fonts CDN, so the UI renders without outbound network access |
| `static/js/` | Vendored `chart.umd.js` (4.4.0) + `chartjs-plugin-datalabels.min.js` (2.x) — no CDN |
| `mockups/dashboard.html` | Standalone static design PoC (sample data, not wired to Flask) |

**Dashboard charts** (`index.html`):
| Chart | Type | Data source |
|---|---|---|
| Platform Split | Horizontal bar (count) | `by_platform` |
| State Split | Horizontal bar (count) | `by_location` |
| By Month | Vertical bar + ChartDataLabels | `by_month` |
| Weekly Revenue | Line | `by_week` |

### Cold-start trap (the forecast fragment can take tens of seconds)

**Fixed for the dashboard on 2026-08-19:** `/` no longer calls `get_prediction()` at all. It renders immediately (measured 0.04s cold / 0.005s warm) with a "Loading live hazard data…" placeholder in `#forecastSlot`; JS then fetches `/partial/forecast` (a server-rendered Jinja fragment, `templates/_forecast.html`), injects it, and only then kicks off `loadAiForecast()` so the AI call reuses the just-warmed feed cache. On fragment failure the slot shows a quiet "Forecast unavailable" card instead of breaking the page.

The underlying slowness still exists — it has just been moved off the critical path. `/partial/forecast` measured 16.9s cold on 2026-08-19 (17.1–59.2s range on 2026-08-15). Feeds are cached for 30 minutes (`CACHE_TTL`), but the cache is **in-process** — every container restart, redeploy, or `docker compose up -d --build` empties it, and the first fragment fetch absorbs the full cost.

The dominant cost is the NIFC burn-scar ArcGIS query, and it scales with the number of distinct customer states:

```
nifc_scars   21.44s   ← 9 real state codes (5 invented ones: 5.6s)
nws           3.82s
usgs          0.80s
```

**`_http_json(url, timeout=6)` does not bound this.** `urllib`'s `timeout` is a per-socket-operation timeout, not a total-request deadline — ArcGIS trickles the response, no individual `recv()` ever exceeds 6s, and the whole request still spans 21s. The `# cold cache ~6s worst case` comment at `prediction.py:622` is wrong for this reason. A real wall-clock bound needs `future.result(timeout=N)` on the existing futures, not the `urllib` timeout.

Note the interaction with gunicorn's `--timeout 60`: a 59.2s cold request is a few hundred milliseconds from having its worker killed.

### Fixed bug (2026-08-19): empty `state_codes` used to crash the prediction

The `_cached` wrapper's empty-`state_codes` fallback returns `[]` for every feed, but `_score_usgs()` expects a dict — so an empty database (or all-unmapped locations) raised `AttributeError: 'list' object has no attribute 'get'`, which 500'd the dashboard. Fixed by normalizing `usgs_data` to `{"by_state": {}, "total_weight": 0.0}` in `get_prediction()` when it isn't a dict; `test_api_prediction_keys` (which stubs `_cached` with a `[]`-returning lambda) now passes.

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
ANTHROPIC_API_KEY=    # /api/ai-forecast; called via urllib, NOT the anthropic SDK
TS_IP=                # tailnet address for the compose port binding
LAN_IP=               # LAN address for the compose port binding (defaults to 127.0.0.1 if unset)
```

`sales-tracker/.env` is gitignored and untracked — verified, the live API key is not in the repo. `.deploy.env` at the repo root is separately gitignored and holds only `DEPLOY_HOST` / `DEPLOY_PATH`.

`/api/ai-forecast` calls the Anthropic API directly over `urllib.request` (no SDK dependency), with its own 30-minute in-process cache (`_AI_CACHE_TTL`) and a `?bust=1` override. It is billed per token, so avoid hammering it in loops.

### UI design notes

**"Tuftler" design system (replaced Floodworks, Aug 2026).** Matches tuftler.co, the product's marketing site. All tokens are CSS custom properties in `base.html`'s `:root` — change them there, not per-page.

- **Two themes:** light default (warm paper `--bg #fffefb` / `--panel #f8f4f0` / `--card #fffefb`, hairline `--line #eee8df`, ink `--fg #201515`) and a warm-dark variant under `:root[data-theme="dark"]` (`#1b1310`/`#251c17`/`#2f2520`). Accent is safety orange `--accent #ff4f00` in both. Toggle button in the nav; choice persists in `localStorage.theme`; an inline `<head>` script sets `data-theme` **before paint** (no flash) and the toggle dispatches a `window` `themechange` event.
- **Fonts:** `--font-display` Mona Sans (headings/eyebrows, weights 500–600, −0.01em on large sizes) + `--font-body` Inter — both self-hosted variable woff2 in `static/fonts/`.
- **Components (base.html):** borderless 12px-radius `.card`/`.kpi` panels; `.eyebrow` (uppercase Mona Sans micro-label with accent dash); `.flip` inverted band (`--flip-*` tokens; ink band on the light theme, paper band on dark) — one per page max, used for the dashboard forecast and the milestones journey band; `.btn-primary` (solid accent) / `.btn-pill` (hairline pill); platform badges are hairline pills with a colored dot (`--plat-amazon/-ebay/-walmart`). Signature element: **block-per-unit progress** on the Milestones page — the next goal renders as one block per state/sale (or 10 blocks of 10% for dollar goals), lit blocks in accent staggering in left to right; Up Next rows use a 10-block mini version (all in `milestones.html`; the old `.vessel` floodwater gauge is gone). Loyal picked this from rendered mockups after rejecting illustrative and instrument-styled (tape measure/odometer) treatments — keep progress UI flat, typographic, and literal.
- **Charts must re-render on theme toggle.** Chart.js snapshots colors at creation, so every page with charts wraps creation in a `renderCharts()` that destroys tracked instances, re-reads CSS vars (`--chart-grid/-tick/-accent-soft/-neutral`, literal rgba per theme — canvas can't parse `color-mix()`), and recreates on `themechange`. The analytics choropleth repaints inline SVG fills in `paintMap()` on the same event (`--map-rgb`). Dataviz palette: max/primary series in accent, everything else neutral.
- Chart.js is loaded per-page (index.html, history.html) — **not** in base.html.
- Staggered `.reveal` entrance + count-ups on load; all motion wrapped in a `prefers-reduced-motion` guard.

Transaction-row conventions (both dashboard preview and full-screen panel):
- Platform **badge** classes (in base.html): `.b-amazon` green, `.b-ebay` cyan, `.b-walmart` yellow; `.b-new` for the NEW pill.
- Platform-colored **left-border accent** (3px `box-shadow: inset 3px 0 0 …` on first `<td>`): Amazon `var(--good)`, eBay `var(--water-2)`, Walmart `var(--hazard-2)`. These are selected via `tr[data-platform="…"]` — every transaction row must have `data-platform="{{ s.platform }}"`.
- "NEW" pulse badge on the most recent row + an orange gradient tint via `tr.row-highlight`.
- The full-screen panel groups rows by month using a Jinja `namespace` to emit `<tr class="month-header" data-month="YYYY-MM">` before the first row of each new month. JS (`enrichTxPanel()`) fills in the label/subtotal/count and draws a per-month orange sparkline onto the inline `<canvas class="mini-spark">` when the panel is first opened. Sparkline rendering is deferred to panel-open because `canvas.clientWidth` is 0 while `display:none`.
- Relative dates ("Today" / "Yesterday" / "Nd ago" within 7 days) are applied client-side to any `<span class="rel-date" data-date="…">` — the raw ISO date goes in `data-date`, server-rendered text is the fallback.

## Asset Folder Conventions

These folders are **gitignored and not present on the server** — they live on the Windows workstation only. The table is a naming convention, not a description of the checked-out tree.

| Folder | Contents |
|---|---|
| `01_raw-media/` | Original unedited photos/videos from phone |
| `02_product-assets/` | Final product photos, graphics, illustrations |
| `03_compressed/` | Resized/compressed exports (1:1 sets for Amazon/eBay) |
| `04_videos/` | Edited video exports |
| `05_insert-paper/` | Product insert / packaging documents |
| `06_documents/` | Quick-use guide PDFs, QR code, feedback forms |
