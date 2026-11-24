# Diabet Logger

Self-hosted tracker for blood glucose, insulin, meals and CGM — with dose calculator and reports for doctor and dietitian.
FastAPI + SQLite + Chart.js, single container.

![Diabet Logger](docs/screenshot.png)

> **⚠️ Disclaimer:** This is a personal hobby project, NOT a certified medical device or clinical software. It is not intended to replace professional medical advice, diagnosis, or treatment. Dose calculations are suggestions only — always verify with your healthcare provider. Use entirely at your own risk.

## Features

- **Entries:** BG (mmol/L), rapid and slow insulin (U), ketones (mmol/L), meals (XE), activity (min), alcohol (units), device change (vial/sensor/patch pump/pump base/settings change)
- **Pump support (Medtrum Nano):** rapid insulin — delivery (pump/pen/IV), insulin type (Fiasp/NovoRapid/other), bolus kind (normal/extended/combo with duration). Slow insulin — delivery and type. History shows icons for delivery and bolus kind
- **BG source:** manual readings — own glucometer, hospital or manual CGM. Hospital readings show 🏥 in history
- **Ketones:** accepts value 0 (for a negative test result)
- **CGM:** readings from Juggluco are pulled from the phone (`juggluco_pull.py`) and stored as regular `bg` entries with note `[CGM]`. Manual BT glucometer readings (e.g. Contour Next One) are pulled from Juggluco `/x/amounts` (TSV, label "Blood", value in mmol/L) and stored with note `[Глюкомер-BT]` and `meta.source: "own"`, with separate watermark and deduplication by time
- **Time-in-Range** (Battelino et al. 2019), **GMI** (Bergenstal 2018), mean BG, SD, CV%
- **Chart** with zoom and pan over time. CGM as thin line, manual readings as dots. Filters by period and source (BG + CGM / manual only / CGM only). Meal markers (XE) and low treatment markers; notes shown on hover
- **History** with inline note editing (value and time are immutable — intentional audit trail)
- **Meal calculator (`/meal`):** dose by XE and current BG, evening dose, rule settings
- **"Low" button:** below the stats — fast carbs with one tap (grams × count), with date/time picker (defaults to now). Stored as `meal` with `carbs_g`, XE per `xe_grams_carbs` setting and note `[HYPO] 5×3g`
- **"Cigarette" button:** count (default 1), note and time. Stored as `activity` (5 min, light) with note `[CIGARETTE] 2 pcs.`, for tracking the effect on blood glucose
- **"Night kit" button:** predefined fast carbs (10g) and optional slow carbs, with time. For treating nighttime lows with a measured amount. Stored as `meal` with note `[HYPO] Night: 10g fast + 8g slow`
- **Pump panel:** four quick-entry buttons — ⚡ Temp Basal (%, duration), 💉 Bolus (units, manual override), ⏸ Suspend (duration), 🔄 Resume. Stored as `device_change` with meta `temp_basal_pct`, `temp_basal_min` or `resume`
- **Patch info:** when changing device type "Patch pump" — fields for filled (U), remaining (U) and body site (abdomen, arm, leg, back — left/right). Shown in history: "Patch · 150U, rem. 45U, arm R"
- **Period tags:** 🏷️ Period button for marking time ranges — 🏥 Hospital, ✈️ Travel, 🤒 Illness, 😰 Stress, 🏃 Active day, 📌 Other. Each period has start/end date and note. Shown as colored band on chart and as entry in history
- **Trend and recent insulin:** next to "Last BG" — arrow and CGM rate for the last ~15 min (→ ↗ ↑ ⇈ / ↘ ↓ ⇊; red at ≥0.11 mmol/L/min). "Rapid 4h" card: sum of rapid insulin for last 4h and time of last dose. For reference only, not used by calculators
- **Daily profile:** day label (Fast / Normal / Slow) and note, for reference only
- **Reports:** `/report` for doctor, `/print-review` for dietitian (A4 print layout)
- **Time zone:** selectable from dropdown in Settings (see "Time zone")
- **Login** (username and password, JWT 30 days) and/or **API key**, both optional
- **Auto-refresh every 60 sec** (pauses while editing a note) and "⟳ Refresh" button that pulls new CGM readings
- **Stream overlay** for OBS — `static/overlay.html` (see "Stream overlay")
- **Export / Import** JSON and CSV. CSV with period selection (From / To, see "Export CSV")
- **i18n:** 9 languages (Bulgarian, English, German, Spanish, French, Portuguese, Italian, Dutch, Polish) with 586 translation keys. Language selector dropdown on main page. To add a language: copy `en.json` → `lang/xx.json`, add the code to `i18n.js`

## Interface

The main page is mobile-first, with dark theme and Sofia Sans font (with Cyrillic letterforms; loaded from Google Fonts — remove the three lines in `<head>` for offline use, falling back to system font).

- **Last reading** as large number colored by zone, with a scale below showing the 3.9–10 target range and where the value falls. Scale is fixed 2–20 mmol/L for stability; above 20 the marker pins to the end.
- **Chart** auto-scales to data. The line changes color by zone (in range, high, low), with the 3.9–10 target range as a band behind it. Temp basal shown as colored band: orange for increased (>100%), blue for decreased (<100%), red for suspended (0%), with percentage label on top. Period tags (hospital, travel, etc.) appear as faint colored strips with icon at bottom.
- **Color means data:** frame and buttons are monochrome; color is reserved for measured and recorded values. New-entry tabs carry the color of the type they record.
- On mobile, bottom bar navigates to sections (Now, Entry, Chart, History, More); on wide screens, entry stays on the left, views on the right.
- **Date/time fields** follow the clock until touched; the "Now" button resets them. On save, an untouched field is the current moment, not the time when the page was opened.
- Opened directly from disk (`file://`, no server), the page enters **demo mode** with fictional data and a banner — for browsing, nothing is saved. Loaded from a server, demo mode does not activate.

## Quick start

```bash
cd diabet-logger
docker compose up -d --build
```

Open `http://<server>:8765/`. No auth by default (acceptable behind WireGuard / LAN).

**Important on changes:** `static/` is not a bind mount — it's baked into the image. After changing `.html` or `.py`, run `docker compose up -d --build`. The database (`data/`) is a bind mount and persists.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `DIABET_USERNAME`, `DIABET_PASSWORD` | – | Browser login (JWT) |
| `DIABET_API_KEY` | – | `X-API-Key` for scripts |
| `DIABET_JWT_SECRET` | auto in `data/.jwt_secret` | JWT signing key; restart doesn't invalidate tokens if `data/` persists |
| `DIABET_TOKEN_TTL_DAYS` | `30` | Token validity |
| `DIABET_TZ` | `Europe/Sofia` | Default timezone for daily stats and reports. Can be changed from Settings (see "Time zone"); times are stored in UTC |
| `JUGGLUCO_HOST` | – | Phone address running Juggluco (reachable via WireGuard). Required for CGM sync |
| `JUGGLUCO_PORT` | `17580` | Juggluco webserver port |
| `JUGGLUCO_API_SECRET` | – | Juggluco `api_secret` (auth in URL path) |
| `DIABET_LOGGER_API_KEY` | = `DIABET_API_KEY` | Optional: separate key for `juggluco_pull.py`. By default it uses `DIABET_API_KEY` |
| `DIABET_DB`, `DIABET_SECRET_FILE` | `/data/...` | Paths inside the container |

`.env` has `600` permissions and never enters git. Without any auth variable the app is open.

## Files

```
app.py                 FastAPI backend
juggluco_pull.py       pulls CGM from Juggluco on the phone (via WireGuard)
juggluco_watermark.json progress of last pull
dedupe.py              finds and cleans duplicate entries, report-only by default (see "Duplicates")
logger2diabetesm.py    converter from Logger CSV to Diabetes:M ENTRIES format for import
static/i18n.js         i18n loader — loads language files, switches with dropdown
static/lang/*.json     translations (bg, en, de, es, fr, pt, it, nl, pl) — for a new language: copy en.json → xx.json, add the code to i18n.js
static/profiles.html   pump profiles: basal, ISF, CR — settings history with comparison
qa.py                  QA tests: python3 qa.py
static/                index.html, meal.html, report.html, print-review.html, cgm_view.html
data/diabet.db         SQLite (bind mount)
```

## Data model

One `entries` table plus helper tables.

| Column | |
|---|---|
| `type` | `bg`, `fast`, `slow`, `ketones`, `meal`, `activity`, `alcohol`, `device_change` |
| `value` | BG/ketones mmol/L, insulin U, meal XE, activity min, alcohol units (`device_change` = 0; subtypes: `ampule`, `sensor`, `patch`, `pump_base`, `settings_change`) |
| `time` | ISO 8601, in UTC |
| `notes` | free text (the only editable field) |
| `meal_time`, `carbs_g`, `meta` | optional: meal start time, grams of carbs, JSON extras (e.g. `{"intensity":"moderate"}` for activity, `{"device_type":"patch"}` for device change, `{"delivery":"pump","insulin_type":"fiasp","bolus_kind":"extended","bolus_duration_min":60}` for rapid insulin, `{"source":"hospital"}` for BG, `{"device_type":"settings_change","temp_basal_pct":150,"temp_basal_min":60}` for temp basal, `{"device_type":"patch","patch_filled_u":200,"patch_remaining_u":45,"patch_site":"abdomen-R"}` for patch, `{"device_type":"period","period_type":"hospital","period_start":"2026-11-09","period_end":"2026-11-13"}` for period) |

- **`UNIQUE(type, time)`:** two entries of the same type at the same time cannot exist. A duplicate POST returns the existing entry with `"duplicate": true`, and import skips duplicates. Consequence: two manual BG readings in the same minute merge into one.
- **`settings`:** key-value. Row `rules` holds the calculator rules (JSON), row `timezone` holds the selected timezone.
- **`day_profiles`:** `date` (local day), `profile` (`fast`/`normal`/`slow`), `notes`. Not included in `/api/export`.
- **`basal_profiles`:** `id`, `name`, `active_from` (YYYY-MM-DD), `basal` (JSON, 24 values U/h), `isf` (JSON, segments `[{from, value}]`), `cr` (JSON, segments `[{from, value}]`), `notes`. Pump settings history — which profile was active when.

## API

Everything under `/api`. Auth (if configured): `X-API-Key: ...` or `Authorization: Bearer <jwt>`. Swagger: `http://<server>:8765/docs`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | health, which auth methods are enabled, timezone |
| POST | `/api/login` | `{username,password}` → `{token,expires_at}` |
| GET | `/api/entries` | `?type=&from=&to=&limit=` (default limit 20000) |
| POST | `/api/entries` | `{type,value,time,notes,meal_time?,carbs_g?,meta?}` |
| PATCH | `/api/entries/{id}` | `{notes}`, notes only |
| DELETE | `/api/entries/{id}` | deletes one entry |
| DELETE | `/api/entries` | deletes ALL entries |
| POST | `/api/cgm-sync` | runs `juggluco_pull.py`; returns last line of output. Timeout 120 sec (504 if phone unreachable) |
| GET | `/api/stats?days=14` | TIR, GMI, mean, SD, CV, insulin per day |
| GET | `/api/report?from=&to=` | full report for doctor |
| GET | `/api/export` | JSON, grouped by type. `bg`, `ketones`, `meal` carry value in `value`, all others in `units` |
| POST | `/api/import` | `{bg:[],fast:[],...,replace:bool}`, merge with duplicate skipping |
| GET / PUT | `/api/settings` | calculator rules (PUT is partial) |
| POST | `/api/settings/reset` | resets rules to defaults |
| POST | `/api/calc-dose` | `{bg,xe}` → recommended dose, breakdown, warnings |
| POST | `/api/calc-evening?bg=` | evening slow dose and snack recommendation |
| GET / PUT / DELETE | `/api/timezone` | read / set `{timezone}` (IANA name, e.g. `Europe/Sofia`) / reset to `DIABET_TZ` |
| GET | `/api/day-profiles` | `?from=&to=` (dates `YYYY-MM-DD`) |
| PUT / DELETE | `/api/day-profiles/{date}` | `{profile,notes}` / delete |
| GET | `/api/day-response?date=` | numbers for the first rapid dose of the day: BG at dose, at +2h and +3h |
| GET | `/api/profiles` | all basal profiles, by `active_from` desc |
| POST | `/api/profiles` | new profile `{name, active_from, basal, isf, cr, notes}` |
| PUT | `/api/profiles/{id}` | edit profile |
| DELETE | `/api/profiles/{id}` | delete |
| GET | `/api/profiles/active` | profile with latest `active_from` ≤ today |

Pages: `/` (main), `/meal`, `/report`, `/print-review`, `/static/profiles.html` (pump profiles).

## Dose calculator

Rules live in the `settings` table and are edited from `/meal` → Settings (grams of carbs per 1 XE can also be set from the main page). **Values below are code defaults. Live values may differ — check in Settings.**

```
dose = XE × I:C + correction
```

| Rule | Default |
|---|---|
| I:C at BG ≥ 5 / at BG < 5 | 3.0 / 2.0 U per 1 XE |
| Correction at BG 10–13 / 13–16 / 16+ | +1 / +2 / +2 U |
| BG < 4 | blocks insulin (hypo) |
| BG ≥ 15 | warning: check ketones |
| rapid insulin within < 2h | stacking warning |
| evening snack if BG ≤ | 6.0 (default "up to 200g yogurt") |
| evening slow dose | 15 U (fixed) |
| 1 XE = | 12 g (can be changed in Settings, e.g. 15 g) |

`/meal` stores a meal as 3 entries with the same timestamp: BG, meal and rapid insulin.

## Daily profile

Day label and note, visible in the "Daily profile" panel and as a row in History. The panel also shows numbers for the first rapid dose of the day (BG at dose, at ~2h and ~3h, change and change per unit; without a meal at the same time, this is a pure correction effect).

**For reference only:** the profile is not read by `calc-dose` or `calc-evening`, does not change doses and is not included in export or reports. The day for `day-response` is determined by the selected timezone.

## Time zone

All times are stored in UTC. The timezone only determines **where the day ends** for daily stats, reports, active-day counting and daily profile. Selected from the dropdown in Settings; if nothing is selected, `DIABET_TZ` applies (default `Europe/Sofia`). Changing it does not rewrite data and takes effect immediately.

The times you see and enter in the browser follow the device's own timezone. When the server and device timezones differ, a warning appears below the menu: the day in reports and the times on screen may not match.

## Reference ranges

**BG (mmol/L):** `<3.0` very low · `3.0–3.8` low · `3.9–10.0` in range · `10.1–13.9` high · `>13.9` very high

**Ketones (blood, β-hydroxybutyrate, mmol/L):**
- `<0.6` normal
- `0.6–1.5` elevated: check BG, hydrate
- `1.5–3.0` DKA risk: contact doctor
- `>3.0` emergency

**TIR targets (T1D):** in range >70%, <3.0 mmol/L under 1%, total hypo under 4%, CV <36%.

**GMI** is an estimate from mean BG and does not replace lab HbA1c. Valid with 14+ days of regular readings. With CGM, every reading counts as a measurement, so TIR and hypo frequency are based on a dense data stream.

## CGM

- `POST /api/cgm-sync` (the "⟳ Refresh" button) runs `juggluco_pull.py`, which pulls new readings from the phone via WireGuard and stores them with note `[CGM]`. Progress is kept in `juggluco_watermark.json`, so each pull is incremental.
- **Configuration without secrets in files:** `JUGGLUCO_HOST`, `JUGGLUCO_PORT` and `JUGGLUCO_API_SECRET` are in `.env` and reach the container through `environment:` in `docker-compose.yml`. The script uses `DIABET_API_KEY` for app access. If a variable is missing, it exits with code 2 and says which one (without values). On the host (manually or via cron) the script also reads `.env` next to itself, with real environment variables taking precedence.
- **The `/static/cgm_view.html` page** (CGM + manual readings) has no key in the code. It prompts for the API key on first open and stores it only in the browser's `localStorage`, under the same keys as other pages (`diabet_api_key`, `diabet_token`). The "API key" button changes it; on denied access, auto-refresh stops.
- Page auto-refresh only reloads from the database. If you want it to also pull from the phone, there's a constant `AUTO_SYNC_CGM` in `index.html` (default `false`).
- Duplicate readings are prevented by `UNIQUE(type, time)`.

## Export CSV

The "Export CSV" button in Settings downloads the **entire** dataset from `/api/export` and filters it in the browser.

- **Period:** "From" and "To" fields next to the button. Empty = everything. Dates are local days, inclusive (00:00 of "From" to 23:59 of "To"). If "From" is after "To", nothing is downloaded.
- **Filename:** `diabet-log-2026-10-24.csv`, or with period `diabet-log-2026-10-01_2026-10-24.csv`.
- **Types:** all — `BG, FAST, SLOW, KETONES, MEAL, ACTIVITY, ALCOHOL, DEVICE_CHANGE`.
- **Columns:** `type, time_utc, time_local, value, unit, notes, carbs_g, meal_time, meta`. `time_utc` is the source of truth; `time_local` is for reading and follows the device timezone. `meta` is JSON (e.g. `{"intensity":"moderate"}`).
- **Units:** BG and ketones mmol/L, meals XE, insulin U, activity min, alcohol count. Values are under 100, except activity — up to 600 minutes.
- **Note markers:** `[CGM]` for sensor readings, `[HYPO]` for low treatment.

## Stream overlay

`static/overlay.html` — a single panel with current glucose for OBS (Browser Source), transparent background, English text.

- **URL:** `http://<server>:8765/static/overlay.html#key=<DIABET_API_KEY>` — the part after `#` doesn't reach the server and doesn't appear in its logs. The key is stored in `localStorage`.
- **Size:** 400×260; for larger and sharper — `&scale=2` and 800×520.
- **Options:** `sync=0` — don't pull from phone, only read database; `every=60` — seconds between refreshes (min. 30).
- **Data:** CGM for the last 3h. On each refresh, first calls `/api/cgm-sync` (pulls from Juggluco), then `/api/entries`.
- **States:** green — normal; yellow — below 5 or drop ≥ 0.11 mmol/L/min; red "LOW · PAUSE" — below 4; grey — data older than 10 min.
- No insulin, no manual readings — shows only what's appropriate for an audience.

## Duplicates

Duplicate = two or more entries with the same `(type, time)`. Occurs when `juggluco_watermark.json` is lost or after a repeated `juggluco_pull.py --full`, and silently skews TIR, mean and GMI.

On startup, the app attempts to create `UNIQUE(type, time)`. If duplicates already exist in the database, the index is not created and the logs show `[warn] UNIQUE(type,time) index NOT created`. Then:

```bash
cp data/diabet.db data/diabet.db.bak                  # backup first
DIABET_DB=data/diabet.db python3 dedupe.py            # report only, changes nothing (dry run)
DIABET_DB=data/diabet.db python3 dedupe.py --apply    # deletes, keeps entry with smallest id, then creates index
docker compose restart
```

`DIABET_DB` defaults to `/data/diabet.db` (path inside the container), so it must be set explicitly from the host. The script shows groups where duplicate entries have **different values**: these are not CGM resends and should be reviewed manually before `--apply`.

## Backup

```bash
# manual
cp data/diabet.db backup/diabet-$(date +%F).db

# cron 03:00 daily
0 3 * * * cp /path/diabet-logger/data/diabet.db /backup/diabet-$(date +\%F).db
```

## nginx reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name diabet.example.com;
    ssl_certificate     /etc/letsencrypt/live/diabet.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/diabet.example.com/privkey.pem;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Sync between devices

One URL, one database. The page refreshes every 60 sec. Login token is valid 30 days, per device.

## Migrations

Automatic on startup: removal of old `CHECK` (v2 → v3), adding columns `meal_time`, `carbs_g`, `meta`, tables `settings`, `day_profiles` and `basal_profiles`. Data is preserved.
