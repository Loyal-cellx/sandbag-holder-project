# Codebase Audit — Fix List

Audit of the sales-tracker app (backend, templates, infra, deploy), 2026-06-10.
Items are ordered by priority. Check them off as they land.

---

## 🔴 P0 — Verified bugs (reproduced)

### 1. `cp .env.example .env` silently corrupts the local DB path
- [ ] Fix `.env.example` + harden `database.py`

The line `DB_PATH=              # optional: ...` is **not** parsed as empty by
python-dotenv — `DB_PATH` becomes the literal string
`"# optional: override SQLite path"`. Local dev then writes sales data to a file
literally named that, in whatever directory the app was launched from. The file
also dodges `.gitignore` (which only covers `sales.db`), and `deploy.sh` does
`git add .`, so sales data could get committed.

Docker is unaffected (compose sets `DB_PATH` explicitly).

**Fix (two lines):**
- `.env.example`: remove the inline comment from the `DB_PATH` line (move it to its own `#` line above).
- `database.py:7`: `os.getenv("DB_PATH") or <default>` instead of `os.getenv("DB_PATH", <default>)` so empty/garbage values fall back.

### 2. One malformed date permanently bricks the dashboard
- [ ] Validate date in `app.py` `/log` POST handler

`/log` validates amount, location, and platform — never the date. A row like
`05/25/2026` (or empty string) makes `get_stats()` throw
(`database.py:166` — SQLite `strftime` returns NULL for unparseable dates),
which 500s `/` and `/api/stats` until the row is manually deleted from SQLite.
The HTML date picker only protects the happy path.

**Fix:** `datetime.strptime(sale_date, "%Y-%m-%d")` check in `app.py` with a
`flash()` error, same pattern as the existing amount check.

---

## 🟠 P1 — Operational risks

### 3. No backups of the sales database
- [ ] Add a nightly backup job on the Pi

The entire sales history is one SQLite file, in a Docker volume, on one SD
card — the most failure-prone storage there is. Highest-value item on this list.

**Fix:** nightly cron on the Pi, e.g.
`docker compose exec sales-tracker sqlite3 /app/data/sales.db ".backup /app/data/backup-$(date +%a).db"`
plus a copy off the SD card (USB drive, another machine over Tailscale, etc.).
Rotating by weekday gives 7 restore points for free.

### 4. No `.dockerignore` — `.env` (SECRET_KEY) baked into image layers
- [ ] Add `.dockerignore`

`COPY . .` in the Dockerfile copies `.env`, `venv/`, any local `sales.db`,
`__pycache__/`, and `mockups/` into the image.

**Fix:** add `sales-tracker/.dockerignore`:
```
.env
venv/
__pycache__/
*.pyc
sales.db
mockups/
```

### 5. Dashboard stalls up to ~20 s on cold cache with slow internet
- [ ] Add `/health` endpoint + point Dockerfile healthcheck at it
- [ ] Run under gunicorn (or at least `threaded=True`)
- [ ] Parallelize the four weather fetches

`get_prediction()` makes up to 4 sequential external API calls (NWS, NIFC ×2,
NHC) at 5 s timeout each, in the request path, on every cache miss (30-min
TTL). The container runs the single-threaded Flask dev server, so everything
else — including the Docker healthcheck, which hits this same `/` route —
blocks behind it (healthcheck timeout is 5 s).

**Fix (three parts, each independently useful):**
1. `app.py`: add a trivial `/health` route (no DB, no network) and point the
   Dockerfile `HEALTHCHECK` at it.
2. Dockerfile `CMD`: gunicorn with 1 worker + a few threads
   (add `gunicorn` to `requirements.txt`).
3. `prediction.py`: run the four `_cached(...)` fetches in a
   `ThreadPoolExecutor` so a cold cache costs ~5 s, not ~20 s.

### 6. `deploy.sh` is a shotgun
- [ ] Make `deploy.sh` safer

Problems: `git add .` stages *everything* untracked; pushes straight to `main`
with no pull-first (fails if remote is ahead); no branch check (running it from
a feature branch pushes a stale local `main`).

**Fix:** check current branch is `main` and bail otherwise; `git pull --rebase
origin main` before pushing; prefer `git add -u` (tracked files only) or show
what's being staged before committing.

---

## 🟡 P2 — Code quality

### 7. Profit constant `49/69` duplicated in 3 places
- [ ] Centralize the profit margin

Currently in `database.get_all_sales()`, `database.get_stats()`, and the JS
amount-edit handler in `templates/index.html`.

**Fix:** single `PROFIT_MARGIN` constant in `database.py`; have
`/sales/<id>/edit` return the recomputed `profit` in its JSON response so the
JS copy can be deleted entirely.

### 8. `datetime.utcnow()` is deprecated
- [ ] `database.py:48` → `datetime.now(timezone.utc)`

Fine on the pinned `python:3.11-slim` image; breaks noisily on 3.12+.

### 9. Inline-edit JS builds inputs via `innerHTML` string-concat
- [ ] Use `createElement` + `.value` in `templates/index.html` edit handlers

Only `"` is escaped when interpolating the current note into the input's
`value` attribute — a note containing `&quot;` round-trips wrong. Not XSS
(attribute is quoted, Jinja autoescaping intact), but fragile.

### 10. Unauthenticated delete/edit endpoints accept cross-origin POSTs
- [ ] Add a cheap origin/content-type check

`/sales/<id>/delete` takes a body-less POST — a malicious page open on a
tailnet device can fire it cross-origin (CORS blocks the response, not the
send). Tiny risk given Tailscale-only access, free to close.

**Fix:** require `Content-Type: application/json` on both POST endpoints (makes
them non-"simple" requests, so the browser preflights and blocks), or check the
`Origin`/`Host` headers match.

### 11. Zero tests
- [ ] Add a small pytest suite for `database.py`

Cheapest high-value coverage: `get_stats()` with empty DB / single sale /
year-boundary weeks (the SQLite `%W` ↔ Python `%W` round-trip in `by_week` is a
classically fragile pattern), `get_milestones()` progression, and the bad-date
validation once #2 is fixed.

### 12. Edit/delete of nonexistent ids return `{"ok": true}`
- [ ] Return 404 when no row matched (`cursor.rowcount`) — minor.

---

## 🟢 P3 — Accessibility (deferred from the redesign)

### 13. Dialog semantics & focus management
- [ ] `role="dialog"` + `aria-modal="true"` on delete modal and tx panel
- [ ] Focus trap + focus restore on close
- [ ] `aria-label` on icon-only buttons (✎ edit, ✕ delete)
- [ ] Replace `alert()` validation in `log_sale.html` with inline error text

---

## ✅ Confirmed healthy (no action)

- All SQL is parametrized — no injection anywhere.
- Jinja autoescaping intact; `tojson` used for JS data.
- Weather fetchers fail soft (return `[]`, cache result, never crash the page).
- Templates guard empty-data states (`by_platform`, `by_location`, etc.).
- Secrets via env vars; `debug=False`; platform allowlist on input.
