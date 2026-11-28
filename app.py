"""
Diabet Logger — FastAPI + SQLite.

Auth options (any combo):
  DIABET_USERNAME + DIABET_PASSWORD   -> browser login (JWT)
  DIABET_API_KEY                       -> X-API-Key header (for scripts)
  Nothing set                          -> open (OK behind WireGuard/LAN)

Entry types: bg (mmol/L), fast (U), slow (U), ketones (mmol/L), meal (XE)
"""
import os
import json
import secrets
import sqlite3
import hmac
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from fastapi import FastAPI, HTTPException, Header, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

# ---------- CONFIG ----------
DB_PATH = os.environ.get("DIABET_DB", "/data/diabet.db")
SECRET_PATH = os.environ.get("DIABET_SECRET_FILE", "/data/.jwt_secret")
API_KEY = os.environ.get("DIABET_API_KEY", "").strip()
USERNAME = os.environ.get("DIABET_USERNAME", "").strip()
PASSWORD = os.environ.get("DIABET_PASSWORD", "").strip()
TOKEN_TTL_DAYS = int(os.environ.get("DIABET_TOKEN_TTL_DAYS", "30"))

VALID_TYPES = ("bg", "fast", "slow", "ketones", "meal", "activity", "alcohol", "device_change")

# Timestamps are stored UTC. Day-level grouping (daily breakdown, active-day
# counts) must use the LOCAL calendar day, otherwise entries between 00:00 and
# 02:59 local time land on the previous day.
LOCAL_TZ_NAME = os.environ.get("DIABET_TZ", "Europe/Sofia")
try:
    LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
except (ZoneInfoNotFoundError, ValueError):
    print(f"[warn] unknown DIABET_TZ={LOCAL_TZ_NAME!r}, falling back to UTC")
    LOCAL_TZ = timezone.utc


# ---------- TIMEZONE (selectable at runtime) ----------
import time as _time
_TZ_CACHE = {"at": 0.0, "tz": None}
_TZ_TTL = 5.0  # seconds; per-row callers (stats/report) hit the cache, other workers pick changes up quickly


def _resolve_tz(name):
    try:
        return ZoneInfo(name)
    except Exception:  # unknown name, path-like input, a directory, ...
        return None


def get_local_tz():
    """Zone for day grouping: DB setting 'timezone' if valid, else the DIABET_TZ default."""
    now = _time.monotonic()
    if _TZ_CACHE["tz"] is not None and now - _TZ_CACHE["at"] < _TZ_TTL:
        return _TZ_CACHE["tz"]
    tz = LOCAL_TZ
    try:
        with db() as c:
            row = c.execute("SELECT value FROM settings WHERE key='timezone'").fetchone()
        if row:
            picked = _resolve_tz(json.loads(row["value"]))
            if picked is not None:
                tz = picked
    except Exception:
        pass
    _TZ_CACHE["tz"], _TZ_CACHE["at"] = tz, now
    return tz


def local_day(iso: str) -> str:
    """YYYY-MM-DD of an ISO timestamp in LOCAL_TZ.

    Legacy rows without an offset are assumed to be UTC.
    """
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return (iso or "")[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_local_tz()).date().isoformat()


def load_or_create_secret() -> str:
    # Env override wins
    env_secret = os.environ.get("DIABET_JWT_SECRET", "").strip()
    if env_secret:
        return env_secret
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "r") as f:
            s = f.read().strip()
            if s:
                return s
    s = secrets.token_urlsafe(48)
    with open(SECRET_PATH, "w") as f:
        f.write(s)
    os.chmod(SECRET_PATH, 0o600)
    return s


JWT_SECRET = load_or_create_secret()
LOGIN_ENABLED = bool(USERNAME and PASSWORD)

app = FastAPI(title="Diabet Logger", version="3.0")

# Позволява fetch() от file:// произход (kiosk презентация) и други LAN клиенти -
# само GET, без credentials, зад WireGuard мрежата не е реален security риск.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------- DB ----------
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate_db():
    """Handle schema changes across versions."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as c:
        # Create fresh table (no CHECK — Pydantic validates type)
        c.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                value REAL NOT NULL,
                time TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_entries_time ON entries(time)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(type)")

        # Settings key-value store (JSON values)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Daily insulin profile (reference only — never feeds dose calculators)
        c.execute("""
            CREATE TABLE IF NOT EXISTS day_profiles (
                date TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Basal profile versions (pump settings history)
        c.execute("""
            CREATE TABLE IF NOT EXISTS basal_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                active_from TEXT NOT NULL,
                basal TEXT NOT NULL,
                isf TEXT NOT NULL DEFAULT '[]',
                cr TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # If we're upgrading from v2 with a CHECK constraint that excludes 'ketones',
        # rebuild the table without CHECK.
        row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'"
        ).fetchone()
        if row and "CHECK" in (row["sql"] or "") and "ketones" not in row["sql"]:
            c.execute("ALTER TABLE entries RENAME TO entries_old")
            c.execute("""
                CREATE TABLE entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    value REAL NOT NULL,
                    time TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute(
                "INSERT INTO entries (id, type, value, time, notes, created_at) "
                "SELECT id, type, value, time, notes, created_at FROM entries_old"
            )
            c.execute("DROP TABLE entries_old")
            c.execute("CREATE INDEX IF NOT EXISTS idx_entries_time ON entries(time)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_entries_type ON entries(type)")

        # Structured-data phase: nullable columns for meal timing, carb grams,
        # and per-type extra fields (JSON). SQLite has no "ADD COLUMN IF NOT
        # EXISTS", so guard each with a duplicate-column check.
        existing_cols = {row["name"] for row in c.execute("PRAGMA table_info(entries)")}
        for col_name, col_type in (
            ("meal_time", "TEXT"),
            ("carbs_g", "REAL"),
            ("meta", "TEXT"),
        ):
            if col_name not in existing_cols:
                c.execute(f"ALTER TABLE entries ADD COLUMN {col_name} {col_type}")

        # (Must run AFTER the meta column exists — the key uses it.)
        # Dedup guard. A row is a duplicate only if type, time, value AND meta
        # (source etc.) are all identical — a CGM/meter re-push or a double tap.
        # A DIFFERENT value in the same minute is a real, separate reading and
        # must be kept (the old UNIQUE(type,time) silently dropped it).
        # COALESCE: SQLite treats NULLs as distinct in UNIQUE, and CGM rows have
        # meta = NULL — without it CGM re-pushes would no longer dedupe.
        # Only an index change: no existing row is rewritten. The old key is
        # stricter, so every existing DB satisfies the new one.
        c.execute("DROP INDEX IF EXISTS idx_entries_type_time")
        try:
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_dedup "
                "ON entries(type, time, value, COALESCE(meta, ''))"
            )
        except sqlite3.IntegrityError:
            groups = c.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT 1 FROM entries GROUP BY type, time, value, COALESCE(meta, '') HAVING COUNT(*) > 1"
                ")"
            ).fetchone()[0]
            print(
                f"[warn] UNIQUE dedup index NOT created — {groups} duplicate "
                f"group(s) in DB. Run 'python3 dedupe.py --apply', then restart."
            )


migrate_db()


# ---------- SETTINGS ----------
DEFAULT_RULES = {
    # Insulin-to-Carbs ratio (U per XE)
    "ic_ratio_normal": 3.0,
    "ic_ratio_low_bg": 2.0,
    "ic_low_bg_threshold": 5.0,   # Below this BG, use ic_ratio_low_bg
    # Correction ranges [min_bg_inclusive, max_bg_exclusive] -> add_units
    "corrections": [
        {"min_bg": 10.0, "max_bg": 13.0, "add_units": 1.0},
        {"min_bg": 13.0, "max_bg": 16.0, "add_units": 2.0},
        {"min_bg": 16.0, "max_bg": 999.0, "add_units": 2.0},
    ],
    # Safety thresholds
    "hypo_threshold": 4.0,          # Below this: block insulin, suggest carbs
    "ketones_check_threshold": 15.0, # At/above this: prompt ketones check
    "insulin_stacking_hours": 2.0,   # Warn if last fast dose is < N hours ago
    # Target range
    "target_bg_low": 5.0,
    "target_bg_high": 6.0,
    # Evening protocol
    "evening_snack_bg_threshold": 6.0,  # BG <= this: suggest snack
    "evening_snack_desc": "до 200g кисело мляко",
    "evening_slow_dose": 15.0,
    # XE conventions (informational)
    "xe_grams_carbs": 12.0,   # 1 XE = 12g carbs (BG/DE standard)
}


def get_settings() -> dict:
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='rules'").fetchone()
        if not row:
            return dict(DEFAULT_RULES)
        try:
            saved = json.loads(row["value"])
        except (ValueError, TypeError):
            return dict(DEFAULT_RULES)
        # Merge defaults with saved (defaults win for missing keys)
        merged = dict(DEFAULT_RULES)
        merged.update(saved)
        return merged


def save_settings(rules: dict) -> dict:
    merged = dict(DEFAULT_RULES)
    merged.update(rules)
    with db() as c:
        c.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('rules', ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (json.dumps(merged, ensure_ascii=False),),
        )
    return merged


class SettingsPayload(BaseModel):
    ic_ratio_normal: Optional[float] = Field(None, gt=0, lt=20)
    ic_ratio_low_bg: Optional[float] = Field(None, gt=0, lt=20)
    ic_low_bg_threshold: Optional[float] = Field(None, gt=0, lt=30)
    corrections: Optional[list] = None
    hypo_threshold: Optional[float] = Field(None, gt=0, lt=10)
    ketones_check_threshold: Optional[float] = Field(None, gt=0, lt=30)
    insulin_stacking_hours: Optional[float] = Field(None, gt=0, lt=24)
    target_bg_low: Optional[float] = Field(None, gt=0, lt=30)
    target_bg_high: Optional[float] = Field(None, gt=0, lt=30)
    evening_snack_bg_threshold: Optional[float] = Field(None, gt=0, lt=30)
    evening_snack_desc: Optional[str] = Field(None, max_length=200)
    evening_slow_dose: Optional[float] = Field(None, gt=0, lt=100)
    xe_grams_carbs: Optional[float] = Field(None, gt=0, lt=100)


# ---------- AUTH ----------
def issue_token(sub: str) -> tuple[str, datetime]:
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
    token = jwt.encode(
        {"sub": sub, "exp": exp, "iat": datetime.now(timezone.utc)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return token, exp


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return False


def check_auth(
    x_api_key: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    # If neither auth method is configured, allow all
    if not API_KEY and not LOGIN_ENABLED:
        return True

    # Try API key first
    if API_KEY and x_api_key and hmac.compare_digest(x_api_key, API_KEY):
        return True

    # Try JWT (Bearer)
    if LOGIN_ENABLED and authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if verify_token(token):
            return True

    raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- CACHING ----------
# static/ is bind-mounted (P5): a changed file must show up on a plain refresh.
# "no-cache" = the browser may keep a copy but must revalidate (ETag → cheap
# 304), so it never runs stale HTML/JS against a newer API.
@app.middleware("http")
async def _no_stale_static(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------- VALIDATION ERRORS ----------
@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    """422 keeps FastAPI's `detail` list (scripts/qa.py rely on it) and adds a
    one-line human `message` for the UI toast."""
    errors = exc.errors()
    first = errors[0] if errors else {}
    msg = str(first.get("msg", "Invalid input")).removeprefix("Value error, ")
    loc = [str(x) for x in first.get("loc", ()) if x != "body"]
    if loc:
        msg = f"{'.'.join(loc)}: {msg}"
    # Log the offending body for entry writes only — /api/login bodies carry a
    # password, so they are never logged. Headers (auth) are never logged.
    if request.url.path.startswith("/api/entries"):
        try:
            body = json.dumps(exc.body, ensure_ascii=False, default=str)[:2000]
        except Exception:
            body = "<unserializable>"
        print(f"[422] {request.method} {request.url.path} {msg} | body={body}", flush=True)
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": errors, "message": msg}),
    )


# ---------- MODELS ----------
# Allowed value per entry type: (min, max, min_exclusive, max_exclusive, unit).
# Real data on NOMAD (2026-11-28) fits all of these. meal/ketones rows use 1e-9
# as a "zero" sentinel, hence meal is just "> 0".
VALUE_RANGES = {
    "bg":            (1,    30,  False, False, "mmol/L"),
    "fast":          (0.05, 50,  False, False, "U"),
    "slow":          (0.05, 50,  False, False, "U"),
    "ketones":       (0,    10,  False, False, "mmol/L"),
    "meal":          (0,    100, True,  True,  "XE"),
    "activity":      (1,    600, False, False, "min"),
    "alcohol":       (0.5,  10,  False, False, "drinks"),
    "device_change": (0,    100, False, True,  ""),
}


class EntryIn(BaseModel):
    type: Literal["bg", "fast", "slow", "ketones", "meal", "activity", "alcohol", "device_change"]
    value: float = Field(0, ge=0)
    time: str
    notes: Optional[str] = ""
    meal_time: Optional[str] = None       # ISO8601 - начало на хранене, отделно от 'time' (момент на дозата)
    carbs_g: Optional[float] = Field(None, gt=0, le=500)
    meta: Optional[dict] = None           # {"intensity": "..."} за activity, {"device_type": "..."} за device_change

    @model_validator(mode="after")
    def _check_value_by_type(self):
        lo, hi, lo_excl, hi_excl, unit = VALUE_RANGES[self.type]
        v = self.value
        too_low = v <= lo if lo_excl else v < lo
        too_high = v >= hi if hi_excl else v > hi
        if too_low or too_high:
            lo_s = f"> {lo:g}" if lo_excl else f"{lo:g}"
            hi_s = f"< {hi:g}" if hi_excl else f"{hi:g}"
            msg = f"{self.type} value must be {lo_s} … {hi_s} {unit}".rstrip() + f" (got {v:g})"
            if self.type == "bg" and v > hi:
                msg += f" — mg/dL? {v:g} mg/dL = {v / 18:.1f} mmol/L"
            raise ValueError(msg)
        return self


class EntryBatchIn(BaseModel):
    """Several entries saved in one transaction (e.g. BG + meal + bolus)."""
    entries: list[EntryIn] = Field(..., min_length=1, max_length=10)


class ImportPayload(BaseModel):
    bg: list = []
    fast: list = []
    slow: list = []
    ketones: list = []
    meal: list = []
    activity: list = []
    alcohol: list = []
    device_change: list = []
    replace: bool = False


class LoginPayload(BaseModel):
    username: str
    password: str


# ---------- API: AUTH ----------
# ---------- API: TIMEZONE ----------
class TimezoneIn(BaseModel):
    timezone: str = Field(..., min_length=1, max_length=64)


@app.get("/api/timezone", dependencies=[Depends(check_auth)])
def api_get_timezone():
    return {"timezone": str(get_local_tz()), "default": LOCAL_TZ_NAME}


@app.put("/api/timezone", dependencies=[Depends(check_auth)])
def api_put_timezone(payload: TimezoneIn):
    name = payload.timezone.strip()
    if _resolve_tz(name) is None:
        raise HTTPException(status_code=400, detail="Unknown timezone")
    with db() as c:
        c.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('timezone', ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (json.dumps(name),),
        )
    _TZ_CACHE["tz"] = None
    return {"timezone": str(get_local_tz()), "default": LOCAL_TZ_NAME}


@app.delete("/api/timezone", dependencies=[Depends(check_auth)])
def api_reset_timezone():
    with db() as c:
        c.execute("DELETE FROM settings WHERE key='timezone'")
    _TZ_CACHE["tz"] = None
    return {"timezone": str(get_local_tz()), "default": LOCAL_TZ_NAME}


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "auth": {
            "login": LOGIN_ENABLED,
            "api_key": bool(API_KEY),
        },
        "timezone": str(get_local_tz()),
    }


@app.post("/api/login")
def login(payload: LoginPayload):
    if not LOGIN_ENABLED:
        raise HTTPException(status_code=400, detail="Login not configured")
    ok_user = hmac.compare_digest(payload.username, USERNAME)
    ok_pass = hmac.compare_digest(payload.password, PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token, exp = issue_token(payload.username)
    return {"token": token, "expires_at": exp.isoformat()}


# ---------- API: SETTINGS ----------
@app.get("/api/settings", dependencies=[Depends(check_auth)])
def api_get_settings():
    return get_settings()


@app.put("/api/settings", dependencies=[Depends(check_auth)])
def api_put_settings(payload: SettingsPayload):
    # Merge only non-None fields
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    # Validate corrections structure if provided
    if "corrections" in updates:
        corr = updates["corrections"]
        if not isinstance(corr, list):
            raise HTTPException(status_code=422, detail="corrections must be a list")
        for c in corr:
            if not isinstance(c, dict) or not all(k in c for k in ("min_bg", "max_bg", "add_units")):
                raise HTTPException(status_code=422, detail="each correction needs min_bg, max_bg, add_units")
    current = get_settings()
    current.update(updates)
    return save_settings(current)


@app.post("/api/settings/reset", dependencies=[Depends(check_auth)])
def api_reset_settings():
    return save_settings(dict(DEFAULT_RULES))


# ---------- API: DOSE CALCULATOR ----------
class DoseCalcPayload(BaseModel):
    bg: float = Field(..., gt=0, le=30)   # mmol/L; > 30 is almost surely mg/dL
    xe: float = Field(..., ge=0, lt=30)


@app.post("/api/calc-dose", dependencies=[Depends(check_auth)])
def calc_dose(payload: DoseCalcPayload):
    """Compute recommended fast insulin dose based on current settings and inputs."""
    s = get_settings()
    bg = payload.bg
    xe = payload.xe

    # Hypo check
    if bg < s["hypo_threshold"]:
        return {
            "blocked": True,
            "reason": "hypo",
            "message": f"Хипогликемия (BG {bg} < {s['hypo_threshold']}). Приеми 15-20g бързи въглехидрати, изчакай 15 мин, ретестирай. Не инжектирай инсулин преди BG > {s['hypo_threshold']}.",
            "recommended_units": 0,
        }

    # I:C ratio
    if bg < s["ic_low_bg_threshold"]:
        ic = s["ic_ratio_low_bg"]
    else:
        ic = s["ic_ratio_normal"]

    # Correction
    correction = 0.0
    for rule in s["corrections"]:
        if rule["min_bg"] <= bg < rule["max_bg"]:
            correction = float(rule["add_units"])
            break

    meal_dose = xe * ic
    total = round(meal_dose + correction, 2)

    warnings = []
    if bg >= s["ketones_check_threshold"]:
        warnings.append({
            "level": "danger",
            "message": f"BG {bg} ≥ {s['ketones_check_threshold']} mmol/L. Изтегли кетони преди инжектиране. При кетони ≥ 1.5 mmol/L → контакт с лекар.",
        })

    # Insulin stacking check — last fast entry within stacking hours
    with db() as c:
        stack_hours = float(s.get("insulin_stacking_hours", 2.0))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=stack_hours)).isoformat()
        recent = c.execute(
            "SELECT value, time FROM entries WHERE type='fast' AND time >= ? ORDER BY time DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
    if recent:
        warnings.append({
            "level": "warn",
            "message": f"Последна бърза доза {recent['value']} U преди < {stack_hours}ч (insulin stacking риск).",
        })

    if total > 20:
        warnings.append({
            "level": "warn",
            "message": f"Изчислена доза > 20 U. Провери преди инжектиране.",
        })

    return {
        "blocked": False,
        "bg": bg,
        "xe": xe,
        "ic_ratio": ic,
        "meal_units": round(meal_dose, 2),
        "correction_units": correction,
        "recommended_units": total,
        "breakdown": f"{xe} XE × {ic} U/XE + {correction} корекция = {total} U",
        "warnings": warnings,
    }


@app.post("/api/calc-evening", dependencies=[Depends(check_auth)])
def calc_evening(bg: float = Query(..., gt=0, le=30)):
    """Evening slow-dose protocol."""
    s = get_settings()
    if bg < s["hypo_threshold"]:
        return {
            "blocked": True,
            "reason": "hypo",
            "message": f"Хипогликемия (BG {bg}). Приеми въглехидрати, изчакай, ретестирай.",
            "snack_recommended": False,
            "slow_dose": 0,
        }
    snack = bg <= s["evening_snack_bg_threshold"]
    return {
        "blocked": False,
        "bg": bg,
        "snack_recommended": snack,
        "snack_desc": s["evening_snack_desc"] if snack else None,
        "slow_dose": s["evening_slow_dose"],
    }



@app.get("/api/entries", dependencies=[Depends(check_auth)])
def list_entries(
    response: Response,
    type: Optional[str] = None,
    limit: Optional[int] = Query(default=None, ge=0),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
):
    """Newest first. No `limit` (or limit=0) = every matching row — the result
    is never silently cut. With an explicit limit, X-Total-Count and
    X-Truncated tell the caller whether rows were left out."""
    from_iso = None
    to_iso = None
    if from_:
        try:
            from_iso = datetime.fromisoformat(from_.replace("Z", "+00:00")).isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'from' date")
    if to:
        try:
            dt_to = datetime.fromisoformat(to.replace("Z", "+00:00"))
            if dt_to.hour == 0 and dt_to.minute == 0 and dt_to.second == 0:
                dt_to = dt_to.replace(hour=23, minute=59, second=59)
            to_iso = dt_to.isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'to' date")

    with db() as c:
        query = "SELECT id, type, value, time, notes, meal_time, carbs_g, meta FROM entries WHERE 1=1"
        params = []
        if type:
            if type not in VALID_TYPES:
                raise HTTPException(status_code=400, detail="Invalid type")
            query += " AND type=?"
            params.append(type)
        if from_iso:
            query += " AND time >= ?"
            params.append(from_iso)
        if to_iso:
            query += " AND time <= ?"
            params.append(to_iso)
        query += " ORDER BY time DESC"
        if limit:
            total = c.execute(f"SELECT COUNT(*) FROM ({query})", params).fetchone()[0]
            query += " LIMIT ?"
            params.append(limit)
            response.headers["X-Total-Count"] = str(total)
            response.headers["X-Truncated"] = "true" if total > limit else "false"
        rows = c.execute(query, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["meta"] = json.loads(d["meta"]) if d["meta"] else None
            result.append(d)
        return result


@app.post("/api/cgm-sync", dependencies=[Depends(check_auth)])
def cgm_sync():
    """Manually trigger an incremental Juggluco CGM sync (pulls new readings
    over WireGuard and inserts them via juggluco_pull.py's own logic)."""
    import subprocess
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "juggluco_pull.py")
    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="CGM sync timed out (phone unreachable?)")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="juggluco_pull.py not found next to app.py")

    out_lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    err_lines = [l for l in result.stderr.strip().splitlines() if l.strip()]

    if result.returncode != 0:
        # On failure the useful line is the last [error] on stderr — the last
        # stdout line is just "[info] requesting ..." and misleads the user.
        reason = next((l for l in reversed(err_lines) if "[error]" in l), err_lines[-1] if err_lines else "no output")
        reason = reason.replace("[error]", "").strip()
        if result.returncode == 2 or "missing config" in reason:
            msg = f"CGM sync is not configured on the server ({reason})"
        elif "failed to fetch from Juggluco" in reason:
            msg = f"Phone (Juggluco) not reachable — check WireGuard / that Juggluco runs. {reason}"
        else:
            msg = f"CGM sync failed: {reason}"
        print(f"[cgm-sync] exit={result.returncode} {msg}", flush=True)
        raise HTTPException(status_code=502, detail=msg)

    # Success: report BOTH results (CGM + meter). The last line alone is the
    # meter part and hid how many CGM readings were pulled.
    done = [l.replace("[done]", "").strip() for l in out_lines if l.startswith("[done]")]
    summary = " · ".join(done) if done else (out_lines[-1] if out_lines else "no output")
    return {"ok": True, "summary": summary, "warnings": err_lines[-5:], "stdout": result.stdout[-4000:]}


def _entry_times(entry: EntryIn, where: str = ""):
    """Parse time/meal_time to ISO. Raises 400 before anything is written."""
    try:
        t = datetime.fromisoformat(entry.time.replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{where}Invalid time format (ISO 8601 expected)")

    meal_time_iso = None
    if entry.meal_time:
        try:
            meal_time_iso = datetime.fromisoformat(entry.meal_time.replace("Z", "+00:00")).isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{where}Invalid meal_time format (ISO 8601 expected)")
    return t, meal_time_iso


def _insert_entry(c, entry: EntryIn, t: str, meal_time_iso):
    """INSERT one entry on an open connection (caller owns the transaction)."""
    meta_json = json.dumps(entry.meta) if entry.meta else None
    try:
        cur = c.execute(
            "INSERT INTO entries (type, value, time, notes, meal_time, carbs_g, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry.type, entry.value, t, entry.notes or "", meal_time_iso, entry.carbs_g, meta_json),
        )
    except sqlite3.IntegrityError:
        # Identical reading already logged (same type, time, value, meta) —
        # CGM resend or double tap. Idempotent: return the existing row.
        row = c.execute(
            "SELECT id, type, value, time, notes, meal_time, carbs_g, meta "
            "FROM entries WHERE type=? AND time=? AND value=? AND COALESCE(meta, '')=COALESCE(?, '')",
            (entry.type, t, entry.value, meta_json),
        ).fetchone()
        if row is None:
            raise
        result = dict(row)
        result["meta"] = json.loads(result["meta"]) if result["meta"] else None
        result["duplicate"] = True
        return result
    return {
        "id": cur.lastrowid, "type": entry.type, "value": entry.value, "time": t,
        "notes": entry.notes or "", "meal_time": meal_time_iso,
        "carbs_g": entry.carbs_g, "meta": entry.meta,
    }


@app.post("/api/entries", dependencies=[Depends(check_auth)])
def add_entry(entry: EntryIn):
    t, meal_time_iso = _entry_times(entry)
    with db() as c:
        return _insert_entry(c, entry, t, meal_time_iso)


@app.post("/api/entries/batch", dependencies=[Depends(check_auth)])
def add_entries_batch(payload: EntryBatchIn):
    """All-or-nothing: every entry is validated (422) and its time parsed (400)
    before the first INSERT, and all INSERTs share one transaction — db() only
    commits if no exception escapes. Duplicates (retry of a saved batch) are
    returned with duplicate=true, same as the single-entry endpoint."""
    times = [_entry_times(e, f"entries.{i}: ") for i, e in enumerate(payload.entries)]
    with db() as c:
        saved = [_insert_entry(c, e, t, mt) for e, (t, mt) in zip(payload.entries, times)]
    return {"entries": saved, "count": len(saved)}


@app.delete("/api/entries/{entry_id}", dependencies=[Depends(check_auth)])
def delete_entry(entry_id: int):
    with db() as c:
        cur = c.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        return {"deleted": entry_id}


class NotesPatch(BaseModel):
    notes: str = Field(..., max_length=2000)


@app.patch("/api/entries/{entry_id}", dependencies=[Depends(check_auth)])
def update_entry_notes(entry_id: int, payload: NotesPatch):
    """
    Update only the notes field. Value and time are intentionally immutable
    to prevent accidental medical data corruption.
    """
    with db() as c:
        cur = c.execute(
            "UPDATE entries SET notes=? WHERE id=?",
            (payload.notes, entry_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
        row = c.execute(
            "SELECT id, type, value, time, notes FROM entries WHERE id=?",
            (entry_id,),
        ).fetchone()
        return dict(row)


@app.delete("/api/entries", dependencies=[Depends(check_auth)])
def clear_all():
    with db() as c:
        c.execute("DELETE FROM entries")
        return {"cleared": True}


# ---------- API: STATS ----------
@app.get("/api/stats", dependencies=[Depends(check_auth)])
def stats(days: int = 14):
    """
    Compute Time-in-Range, average BG, and GMI over the last N days.

    TIR bands (Battelino et al. 2019, international consensus, mmol/L):
        very_low  : < 3.0
        low       : 3.0 – 3.8
        in_range  : 3.9 – 10.0  (target > 70%)
        high      : 10.1 – 13.9
        very_high : > 13.9

    GMI (Glucose Management Indicator, Bergenstal 2018):
        GMI(%) = 3.31 + 0.02392 × mean_glucose(mg/dL)
        Requires ~14 days of frequent BG data for reliability.
    """
    if days < 1:
        days = 1
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    with db() as c:
        rows = c.execute(
            "SELECT value FROM entries WHERE type='bg' AND time >= ?", (cutoff,)
        ).fetchall()
        insulin_rows = c.execute(
            "SELECT type, value FROM entries WHERE type IN ('fast','slow') AND time >= ?",
            (cutoff,),
        ).fetchall()
        ketone_rows = c.execute(
            "SELECT value FROM entries WHERE type='ketones' AND time >= ?", (cutoff,)
        ).fetchall()

    values = [r["value"] for r in rows]
    n = len(values)

    bands = {"very_low": 0, "low": 0, "in_range": 0, "high": 0, "very_high": 0}
    for v in values:
        if v < 3.0:
            bands["very_low"] += 1
        elif v < 3.9:
            bands["low"] += 1
        elif v <= 10.0:
            bands["in_range"] += 1
        elif v <= 13.9:
            bands["high"] += 1
        else:
            bands["very_high"] += 1

    pct = {k: (v * 100.0 / n) if n else 0.0 for k, v in bands.items()}

    if n:
        mean_mmol = sum(values) / n
        mean_mgdl = mean_mmol * 18.018
        gmi = 3.31 + 0.02392 * mean_mgdl
        std = (sum((x - mean_mmol) ** 2 for x in values) / n) ** 0.5
        cv = (std / mean_mmol) * 100.0 if mean_mmol else 0
    else:
        mean_mmol = None
        gmi = None
        std = None
        cv = None

    fast_total = sum(r["value"] for r in insulin_rows if r["type"] == "fast")
    slow_total = sum(r["value"] for r in insulin_rows if r["type"] == "slow")
    max_ketones = max((r["value"] for r in ketone_rows), default=None)

    return {
        "days": days,
        "bg_count": n,
        "mean_bg": round(mean_mmol, 2) if mean_mmol is not None else None,
        "std_bg": round(std, 2) if std is not None else None,
        "cv_pct": round(cv, 1) if cv is not None else None,
        "gmi_pct": round(gmi, 2) if gmi is not None else None,
        "tir_bands": bands,
        "tir_pct": {k: round(v, 1) for k, v in pct.items()},
        "insulin_total": {
            "fast": round(fast_total, 1),
            "slow": round(slow_total, 1),
            "daily_avg_fast": round(fast_total / days, 1),
            "daily_avg_slow": round(slow_total / days, 1),
        },
        "ketones_max": max_ketones,
    }


# ---------- API: EXPORT / IMPORT ----------
# ---------- REPORT: EPISODES (P4) ----------
EPISODE_GAP_MIN = 15      # readings of the same kind ≤ 15 min apart = one episode
METER_WINDOW_MIN = 15     # meter (finger-stick) reading within ±15 min of an episode


def _utc(ts: str) -> datetime:
    """Parse a stored time; a time without offset is taken as UTC (the API
    accepts naive ISO strings). Mixing naive and aware datetimes would raise
    TypeError when sorting/subtracting and the whole report would 500."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_meter(r) -> bool:
    """A finger-stick BG: anything that is not a CGM reading."""
    notes = (r["notes"] or "")
    if notes.startswith("[CGM]"):
        return False
    try:
        meta = json.loads(r["meta"]) if r["meta"] else {}
    except (ValueError, TypeError):
        meta = {}
    return meta.get("source") != "cgm_manual"


def _episodes(bg_rows, in_episode, worst, meter_rows):
    """Group consecutive qualifying readings into episodes. Purely descriptive —
    nothing here feeds a calculator. `worst` picks min (hypo) or max (hyper)."""
    pts = sorted(
        ((_utc(r["time"]), r) for r in bg_rows if in_episode(r["value"])),
        key=lambda x: x[0],
    )
    meters = sorted(((_utc(r["time"]), r) for r in meter_rows), key=lambda x: x[0])
    groups = []
    for t, r in pts:
        if groups and (t - groups[-1][-1][0]) <= timedelta(minutes=EPISODE_GAP_MIN):
            groups[-1].append((t, r))
        else:
            groups.append([(t, r)])
    out = []
    win = timedelta(minutes=METER_WINDOW_MIN)
    for g in groups:
        start, end = g[0][0], g[-1][0]
        wt, wr = worst(g, key=lambda x: x[1]["value"])
        near = [(abs((mt - wt).total_seconds()), mt, mr) for mt, mr in meters if start - win <= mt <= end + win]
        meter = None
        if near:
            _, mt, mr = min(near, key=lambda x: x[0])
            meter = {"time": mr["time"], "value": mr["value"],
                     "minutes_from_worst": round((mt - wt).total_seconds() / 60)}
        out.append({
            "start": g[0][1]["time"], "end": g[-1][1]["time"],
            "duration_min": round((end - start).total_seconds() / 60),
            "worst_time": wr["time"], "worst_value": wr["value"],
            "readings": len(g), "meter": meter,
        })
    return out


@app.get("/api/report", dependencies=[Depends(check_auth)])
def report(
    from_: str = Query(alias="from"),
    to: Optional[str] = None,
):
    """
    Comprehensive report for a date range.
    Query params: from=ISO_date, to=ISO_date (defaults to now).
    """
    try:
        dt_from = datetime.fromisoformat(from_.replace("Z", "+00:00"))
        if to:
            dt_to = datetime.fromisoformat(to.replace("Z", "+00:00"))
        else:
            dt_to = datetime.now(timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    # Normalize to end-of-day for 'to' if only date given (00:00:00 becomes 23:59:59)
    if to and dt_to.hour == 0 and dt_to.minute == 0 and dt_to.second == 0:
        dt_to = dt_to.replace(hour=23, minute=59, second=59)

    from_iso = dt_from.isoformat()
    to_iso = dt_to.isoformat()
    days_span = max(1, (dt_to - dt_from).days + 1)

    with db() as c:
        rows = c.execute(
            "SELECT id, type, value, time, notes, meta FROM entries "
            "WHERE time >= ? AND time <= ? ORDER BY time",
            (from_iso, to_iso),
        ).fetchall()

    bg = [dict(r) for r in rows if r["type"] == "bg"]
    fast = [dict(r) for r in rows if r["type"] == "fast"]
    slow = [dict(r) for r in rows if r["type"] == "slow"]
    ketones = [dict(r) for r in rows if r["type"] == "ketones"]
    meals = [dict(r) for r in rows if r["type"] == "meal"]

    # ---- BG stats & TIR ----
    bg_vals = [r["value"] for r in bg]
    n = len(bg_vals)
    bands = {"very_low": 0, "low": 0, "in_range": 0, "high": 0, "very_high": 0}
    for v in bg_vals:
        if v < 3.0: bands["very_low"] += 1
        elif v < 3.9: bands["low"] += 1
        elif v <= 10.0: bands["in_range"] += 1
        elif v <= 13.9: bands["high"] += 1
        else: bands["very_high"] += 1

    if n:
        mean_bg = sum(bg_vals) / n
        sd_bg = (sum((v - mean_bg) ** 2 for v in bg_vals) / n) ** 0.5
        cv = (sd_bg / mean_bg) * 100.0 if mean_bg else 0
        gmi = 3.31 + 0.02392 * mean_bg * 18.018
        min_bg = min(bg_vals)
        max_bg = max(bg_vals)
    else:
        mean_bg = sd_bg = cv = gmi = min_bg = max_bg = None

    tir_pct = {k: round(v * 100.0 / n, 1) if n else 0.0 for k, v in bands.items()}

    # ---- Hypoglycemia ----
    hypo_all = sorted(
        [r for r in bg if r["value"] < 3.9],
        key=lambda r: r["value"],
    )
    hypo_severe = [r for r in bg if r["value"] < 3.0]
    hypo_l1 = [r for r in bg if 3.0 <= r["value"] < 3.9]

    # ---- Hyperglycemia ----
    hyper_severe = [r for r in bg if r["value"] > 13.9]

    # ---- Episodes (P4): readings ≤ 15 min apart grouped; nearest meter reading ----
    meter_bg = [r for r in bg if _is_meter(r)]
    hypo_eps = _episodes(bg, lambda v: v < 3.9, min, meter_bg)
    hyper_eps = _episodes(bg, lambda v: v > 13.9, max, meter_bg)

    # ---- Ketones ----
    ketones_max = max((r["value"] for r in ketones), default=None)
    ketones_elevated = [r for r in ketones if r["value"] >= 0.6]
    ketones_dka_risk = [r for r in ketones if r["value"] >= 1.5]

    # ---- Insulin ----
    fast_total = sum(r["value"] for r in fast)
    slow_total = sum(r["value"] for r in slow)
    tdd = fast_total + slow_total
    if tdd > 0:
        basal_pct = round(slow_total * 100.0 / tdd, 1)
        bolus_pct = round(fast_total * 100.0 / tdd, 1)
    else:
        basal_pct = bolus_pct = 0.0

    # Days on which each insulin type was actually injected (local calendar day)
    fast_days = len({local_day(r["time"]) for r in fast})
    slow_days = len({local_day(r["time"]) for r in slow})
    any_insulin_days = len({local_day(r["time"]) for r in fast + slow})

    # ---- Meals / XE ----
    xe_total = sum(r["value"] for r in meals)
    meal_days = len({local_day(r["time"]) for r in meals})
    # Actual I:C ratio: fast total / XE total (if both > 0)
    actual_ic = round(fast_total / xe_total, 2) if xe_total > 0 else None

    # ---- Daily breakdown ----
    daily = {}
    for r in rows:
        day = local_day(r["time"])  # local YYYY-MM-DD, not raw UTC substring
        d = daily.setdefault(day, {"bg": [], "fast": 0, "slow": 0, "ketones": [], "xe": 0})
        if r["type"] == "bg":
            d["bg"].append(r["value"])
        elif r["type"] == "fast":
            d["fast"] += r["value"]
        elif r["type"] == "slow":
            d["slow"] += r["value"]
        elif r["type"] == "ketones":
            d["ketones"].append(r["value"])
        elif r["type"] == "meal":
            d["xe"] += r["value"]

    daily_list = []
    for day, d in sorted(daily.items()):
        bg_day = d["bg"]
        daily_list.append({
            "date": day,
            "bg_count": len(bg_day),
            "bg_min": round(min(bg_day), 1) if bg_day else None,
            "bg_max": round(max(bg_day), 1) if bg_day else None,
            "bg_mean": round(sum(bg_day) / len(bg_day), 1) if bg_day else None,
            "fast": round(d["fast"], 1),
            "slow": round(d["slow"], 1),
            "tdd": round(d["fast"] + d["slow"], 1),
            "ketones_max": round(max(d["ketones"]), 2) if d["ketones"] else None,
            "xe": round(d["xe"], 1) if d["xe"] > 0 else None,
        })

    # ---- Notes list (entries with non-empty notes) ----
    notes_list = [
        {"time": r["time"], "type": r["type"], "value": r["value"], "notes": r["notes"]}
        for r in rows if r["notes"] and r["notes"].strip()
    ]

    return {
        "period": {
            "from": from_iso,
            "to": to_iso,
            "days_span": days_span,
        },
        "counts": {
            "bg": len(bg),
            "fast": len(fast),
            "slow": len(slow),
            "ketones": len(ketones),
            "meals": len(meals),
            "bg_per_day": round(len(bg) / days_span, 1) if days_span else 0,
        },
        "bg_stats": {
            "mean": round(mean_bg, 2) if mean_bg is not None else None,
            "sd": round(sd_bg, 2) if sd_bg is not None else None,
            "cv_pct": round(cv, 1) if cv is not None else None,
            "gmi_pct": round(gmi, 2) if gmi is not None else None,
            "min": round(min_bg, 1) if min_bg is not None else None,
            "max": round(max_bg, 1) if max_bg is not None else None,
        },
        "tir": {
            "bands": bands,
            "pct": tir_pct,
        },
        "hypo": {
            "count_total": len(hypo_all),
            "count_l1": len(hypo_l1),
            "count_severe": len(hypo_severe),
            "worst": [
                {"time": r["time"], "value": r["value"], "notes": r["notes"]}
                for r in hypo_all[:10]
            ],
            "episode_count": len(hypo_eps),
            "episode_count_severe": sum(1 for e in hypo_eps if e["worst_value"] < 3.0),
            "episodes": sorted(hypo_eps, key=lambda e: e["start"], reverse=True)[:50],
        },
        "hyper": {
            "count_severe": len(hyper_severe),
            "highest": [
                {"time": r["time"], "value": r["value"], "notes": r["notes"]}
                for r in sorted(hyper_severe, key=lambda r: -r["value"])[:10]
            ],
            "episode_count": len(hyper_eps),
            "episodes": sorted(hyper_eps, key=lambda e: e["start"], reverse=True)[:50],
        },
        "ketones": {
            "max": ketones_max,
            "count_elevated": len(ketones_elevated),
            "count_dka_risk": len(ketones_dka_risk),
            "entries": [
                {"time": r["time"], "value": r["value"], "notes": r["notes"]}
                for r in sorted(ketones, key=lambda r: -r["value"])[:20]
            ],
        },
        "insulin": {
            "fast_total": round(fast_total, 1),
            "slow_total": round(slow_total, 1),
            "tdd_total": round(tdd, 1),
            "tdd_avg": round(tdd / days_span, 1) if days_span else 0,
            "fast_per_day": round(fast_total / days_span, 1) if days_span else 0,
            "slow_per_day": round(slow_total / days_span, 1) if days_span else 0,
            "basal_pct": basal_pct,
            "bolus_pct": bolus_pct,
            # Averages over ACTIVE days (days with at least one entry of that type)
            "fast_active_days": fast_days,
            "slow_active_days": slow_days,
            "tdd_active_days": any_insulin_days,
            "fast_per_active_day": round(fast_total / fast_days, 1) if fast_days else 0,
            "slow_per_active_day": round(slow_total / slow_days, 1) if slow_days else 0,
            "tdd_per_active_day": round(tdd / any_insulin_days, 1) if any_insulin_days else 0,
        },
        "daily": daily_list,
        "notes": notes_list,
        "meals": {
            "count": len(meals),
            "xe_total": round(xe_total, 1),
            "xe_per_day": round(xe_total / days_span, 1) if days_span else 0,
            "xe_per_active_day": round(xe_total / meal_days, 1) if meal_days else 0,
            "active_days": meal_days,
            "actual_ic_ratio": actual_ic,  # empirical: fast_total / xe_total
            "entries": [
                {"time": r["time"], "value": r["value"], "notes": r["notes"]}
                for r in meals
            ],
        },
    }


@app.get("/api/export", dependencies=[Depends(check_auth)])
def export_all():
    with db() as c:
        rows = c.execute(
            "SELECT id, type, value, time, notes, meal_time, carbs_g, meta "
            "FROM entries ORDER BY time"
        ).fetchall()
        data = {t: [] for t in VALID_TYPES}
        for r in rows:
            item = {"id": r["id"], "time": r["time"], "notes": r["notes"] or ""}
            if r["type"] in ("bg", "ketones", "meal"):
                item["value"] = r["value"]
            else:
                item["units"] = r["value"]
            if r["meal_time"]:
                item["meal_time"] = r["meal_time"]
            if r["carbs_g"] is not None:
                item["carbs_g"] = r["carbs_g"]
            if r["meta"]:
                item["meta"] = json.loads(r["meta"])
            data.setdefault(r["type"], []).append(item)
        return data


@app.post("/api/import", dependencies=[Depends(check_auth)])
def import_data(payload: ImportPayload):
    with db() as c:
        if payload.replace:
            c.execute("DELETE FROM entries")
        added = 0
        skipped = 0
        for t in VALID_TYPES:
            for item in getattr(payload, t, []):
                if "value" in item:
                    val = item.get("value")
                elif "units" in item:
                    val = item.get("units")
                elif t == "device_change":
                    val = 0  # device_change няма числова стойност по природа
                else:
                    val = None
                if val is None or "time" not in item:
                    skipped += 1
                    continue
                try:
                    iso = datetime.fromisoformat(item["time"].replace("Z", "+00:00")).isoformat()
                except (ValueError, AttributeError):
                    skipped += 1
                    continue
                meal_time_iso = None
                if item.get("meal_time"):
                    try:
                        meal_time_iso = datetime.fromisoformat(
                            item["meal_time"].replace("Z", "+00:00")
                        ).isoformat()
                    except (ValueError, AttributeError):
                        pass  # невалиден meal_time не блокира вноса на самия запис
                carbs_g = item.get("carbs_g")
                meta_json = json.dumps(item["meta"]) if item.get("meta") else None
                # OR IGNORE: re-importing an overlapping export is a merge, not an error
                cur = c.execute(
                    "INSERT OR IGNORE INTO entries (type, value, time, notes, meal_time, carbs_g, meta) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (t, val, iso, item.get("notes", ""), meal_time_iso, carbs_g, meta_json),
                )
                if cur.rowcount:
                    added += 1
                else:
                    skipped += 1
        return {"imported": added, "skipped": skipped, "replaced": payload.replace}


# ---------- STATIC ----------
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/report")
def report_page():
    return FileResponse(os.path.join(STATIC_DIR, "report.html"))


@app.get("/meal")
def meal_page():
    return FileResponse(os.path.join(STATIC_DIR, "meal.html"))


@app.get("/print-review")
def print_review_page():
    return FileResponse(os.path.join(STATIC_DIR, "print-review.html"))


# ---------- DAY PROFILE (reference only) ----------

# A per-day label (fast / normal / slow) describing how quickly rapid insulin
# seemed to act that day, plus a note. It is purely informational: nothing here
# is read by /api/calc-dose or /api/calc-evening, by design — basal titration
# stays on the morning-BG rule agreed with the endocrinologist.
class DayProfileIn(BaseModel):
    profile: Literal["fast", "normal", "slow"]
    notes: Optional[str] = Field("", max_length=2000)


class BasalProfileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    active_from: str  # YYYY-MM-DD
    basal: list  # 24 floats, one per hour
    isf: Optional[list] = []  # list of {from: "HH:MM", value: float}
    cr: Optional[list] = []  # list of {from: "HH:MM", value: float}
    notes: Optional[str] = Field("", max_length=2000)


def _valid_day(day: str) -> str:
    try:
        return datetime.strptime(day, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


def _parse_iso_utc(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@app.get("/api/day-profiles", dependencies=[Depends(check_auth)])
def list_day_profiles(
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = None,
):
    q = "SELECT date, profile, notes FROM day_profiles WHERE 1=1"
    params = []
    if from_:
        q += " AND date >= ?"
        params.append(_valid_day(from_))
    if to:
        q += " AND date <= ?"
        params.append(_valid_day(to))
    q += " ORDER BY date DESC"
    with db() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


@app.put("/api/day-profiles/{day}", dependencies=[Depends(check_auth)])
def put_day_profile(day: str, payload: DayProfileIn):
    day = _valid_day(day)
    with db() as c:
        c.execute(
            "INSERT INTO day_profiles (date, profile, notes) VALUES (?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET profile=excluded.profile, "
            "notes=excluded.notes, updated_at=CURRENT_TIMESTAMP",
            (day, payload.profile, payload.notes or ""),
        )
    return {"date": day, "profile": payload.profile, "notes": payload.notes or ""}


@app.delete("/api/day-profiles/{day}", dependencies=[Depends(check_auth)])
def delete_day_profile(day: str):
    day = _valid_day(day)
    with db() as c:
        cur = c.execute("DELETE FROM day_profiles WHERE date=?", (day,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": day}


@app.get("/api/day-response", dependencies=[Depends(check_auth)])
def day_response(date: Optional[str] = None):
    """
    Reference numbers for "how fast did insulin act today": takes the FIRST
    rapid-acting dose of the local day and reports the BG at the dose and
    ~2h / ~3h later (nearest reading within +-15 min; CGM readings count).
    Numbers only — no label is assigned and nothing feeds the calculators.
    """
    ref_day = (
        datetime.strptime(_valid_day(date), "%Y-%m-%d").date()
        if date else datetime.now(get_local_tz()).date()
    )
    day_start = datetime.combine(ref_day, datetime.min.time(), tzinfo=get_local_tz())
    day_end = day_start + timedelta(days=1)
    lo = (day_start - timedelta(days=1)).astimezone(timezone.utc).isoformat()
    hi = (day_end + timedelta(days=1)).astimezone(timezone.utc).isoformat()

    with db() as c:
        rows = c.execute(
            "SELECT id, type, value, time, notes FROM entries "
            "WHERE time >= ? AND time <= ? AND type IN ('bg','fast','meal') ORDER BY time ASC",
            (lo, hi),
        ).fetchall()

    items = [(r, _parse_iso_utc(r["time"])) for r in rows]
    fasts = [(r, t) for r, t in items if r["type"] == "fast" and day_start <= t < day_end]
    if not fasts:
        return {"date": ref_day.isoformat(), "dose": None, "reference_only": True}

    dose_row, t0 = fasts[0]
    bgs = [(r["value"], t) for r, t in items if r["type"] == "bg"]

    def nearest_bg(target: datetime, tol_min: int):
        best = None
        for v, t in bgs:
            d = abs((t - target).total_seconds()) / 60.0
            if d <= tol_min and (best is None or d < best[0]):
                best = (d, v, t)
        return best

    food_xe = None
    for r, t in items:
        if r["type"] == "meal" and abs((t - t0).total_seconds()) <= 120:
            food_xe = r["value"]
            break

    base = nearest_bg(t0, 20)
    base_bg = base[1] if base else None
    points = []
    for after_min in (120, 180):
        hit = nearest_bg(t0 + timedelta(minutes=after_min), 15)
        entry = {"after_min": after_min, "bg": None, "delta": None, "delta_per_unit": None}
        if hit:
            entry["bg"] = hit[1]
            if base_bg is not None:
                entry["delta"] = round(hit[1] - base_bg, 1)
                if food_xe is None and dose_row["value"] > 0:
                    entry["delta_per_unit"] = round(entry["delta"] / dose_row["value"], 2)
        points.append(entry)

    return {
        "date": ref_day.isoformat(),
        "reference_only": True,
        "dose": {
            "id": dose_row["id"], "units": dose_row["value"],
            "time": t0.isoformat(), "notes": dose_row["notes"],
        },
        "food_xe": food_xe,
        "bg_at_dose": base_bg,
        "points": points,
        "note": (
            "С храна в същото време — Δ/U не е чиста корекция." if food_xe is not None
            else "Без храна в същото време — Δ/U е чист корекционен ефект."
        ),
    }


# ============= BASAL PROFILES =============

@app.get("/api/profiles", dependencies=[Depends(check_auth)])
def list_profiles():
    with db() as c:
        rows = c.execute(
            "SELECT id, name, active_from, basal, isf, cr, notes, created_at "
            "FROM basal_profiles ORDER BY active_from DESC"
        ).fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r["id"], "name": r["name"], "active_from": r["active_from"],
            "basal": json.loads(r["basal"]), "isf": json.loads(r["isf"]),
            "cr": json.loads(r["cr"]), "notes": r["notes"],
            "created_at": r["created_at"],
        })
    return result


@app.post("/api/profiles", dependencies=[Depends(check_auth)])
def create_profile(p: BasalProfileIn):
    active = _valid_day(p.active_from)
    if len(p.basal) != 24:
        raise HTTPException(status_code=400, detail="basal must have exactly 24 values (one per hour)")
    with db() as c:
        cur = c.execute(
            "INSERT INTO basal_profiles (name, active_from, basal, isf, cr, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (p.name, active, json.dumps(p.basal), json.dumps(p.isf or []),
             json.dumps(p.cr or []), p.notes or ""),
        )
    return {"id": cur.lastrowid, "name": p.name, "active_from": active}


@app.put("/api/profiles/{pid}", dependencies=[Depends(check_auth)])
def update_profile(pid: int, p: BasalProfileIn):
    active = _valid_day(p.active_from)
    if len(p.basal) != 24:
        raise HTTPException(status_code=400, detail="basal must have exactly 24 values (one per hour)")
    with db() as c:
        cur = c.execute(
            "UPDATE basal_profiles SET name=?, active_from=?, basal=?, isf=?, cr=?, notes=? "
            "WHERE id=?",
            (p.name, active, json.dumps(p.basal), json.dumps(p.isf or []),
             json.dumps(p.cr or []), p.notes or "", pid),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Profile not found")
    return {"id": pid, "name": p.name, "active_from": active}


@app.delete("/api/profiles/{pid}", dependencies=[Depends(check_auth)])
def delete_profile(pid: int):
    with db() as c:
        cur = c.execute("DELETE FROM basal_profiles WHERE id=?", (pid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Profile not found")
    return {"deleted": pid}


@app.get("/api/profiles/active", dependencies=[Depends(check_auth)])
def active_profile():
    """Return the profile with the latest active_from that is <= today."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as c:
        row = c.execute(
            "SELECT id, name, active_from, basal, isf, cr, notes "
            "FROM basal_profiles WHERE active_from <= ? ORDER BY active_from DESC LIMIT 1",
            (today,),
        ).fetchone()
    if not row:
        return {"active": None}
    return {
        "active": {
            "id": row["id"], "name": row["name"], "active_from": row["active_from"],
            "basal": json.loads(row["basal"]), "isf": json.loads(row["isf"]),
            "cr": json.loads(row["cr"]), "notes": row["notes"],
        }
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
