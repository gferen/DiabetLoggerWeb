#!/usr/bin/env python3
"""
Juggluco -> Diabet Logger CGM sync script.

Pulls glucose readings from Juggluco's local webserver (over WireGuard)
and forwards new entries to the Diabet Logger /api/entries endpoint.

Usage:
  python3 juggluco_pull.py            # incremental pull (since last watermark)
  python3 juggluco_pull.py --full     # full backfill from sensor activation
  python3 juggluco_pull.py --manual   # same as default, for manual/cron-triggered runs

Cron example (daily at 03:00):
  0 3 * * * /usr/bin/python3 /path/to/juggluco_pull.py >> /var/log/juggluco_pull.log 2>&1
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ---- CONFIG: everything comes from the environment, no secrets live in this file ----
#
#   JUGGLUCO_HOST         address of the phone running Juggluco              (required)
#   JUGGLUCO_PORT         Juggluco webserver port                            (default 17580)
#   JUGGLUCO_API_SECRET   Juggluco api_secret (path-based auth)              (required)
#   DIABET_API_KEY        same key the app uses; reused for x-api-key        (required)
#   DIABET_LOGGER_API_KEY optional: overrides DIABET_API_KEY for this script
#   DIABET_LOGGER_URL     entries endpoint (host default below; inside the container use port 8000)
#
# Inside the container these arrive via docker-compose.yml (environment:).
# On the host, a .env next to this script is read as a fallback.

def _load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from a .env file; real environment variables always win."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).parent / ".env")

JUGGLUCO_HOST = os.environ.get("JUGGLUCO_HOST", "").strip()
JUGGLUCO_PORT = os.environ.get("JUGGLUCO_PORT", "").strip() or "17580"
API_SECRET = os.environ.get("JUGGLUCO_API_SECRET", "").strip()

BASE_URL = f"http://{JUGGLUCO_HOST}:{JUGGLUCO_PORT}/{API_SECRET}"  # contains the secret: never print or log it

DIABET_LOGGER_URL = os.environ.get(
    "DIABET_LOGGER_URL",
    "http://127.0.0.1:8765/api/entries"  # host-side default; inside the container use port 8000
)
DIABET_LOGGER_API_KEY = (
    os.environ.get("DIABET_LOGGER_API_KEY") or os.environ.get("DIABET_API_KEY") or ""
).strip()


def _require_config() -> None:
    missing = [name for name, value in (
        ("JUGGLUCO_HOST", JUGGLUCO_HOST),
        ("JUGGLUCO_API_SECRET", API_SECRET),
        ("DIABET_API_KEY", DIABET_LOGGER_API_KEY),
    ) if not value]
    if missing:
        print(f"[error] missing config: {', '.join(missing)} (set in .env / docker-compose environment)",
              file=sys.stderr)
        sys.exit(2)

WATERMARK_FILE = Path(__file__).parent / "juggluco_watermark.json"
MAX_COUNT = 50000  # safety cap per request, same as used in the HTML page

# ---- helpers ----

def mmol(sgv_mgdl: float) -> float:
    return round(sgv_mgdl / 18.0182, 1)


def load_watermark() -> int:
    """Returns last successfully synced timestamp (ms epoch), or 0 if none."""
    if WATERMARK_FILE.exists():
        try:
            data = json.loads(WATERMARK_FILE.read_text())
            return int(data.get("last_synced_ms", 0))
        except Exception:
            return 0
    return 0


def save_watermark(ts_ms: int):
    WATERMARK_FILE.write_text(json.dumps({"last_synced_ms": ts_ms}))


def fetch_sensor_start_ms() -> int | None:
    url = f"{BASE_URL}/sgv.json?count=1&sensor=Y&brief_mode=Y"
    try:
        data = _get_json(url)
        if data and isinstance(data, list) and data[0]:
            entry = data[0]
            if "sensorStartDate" in entry:
                dt = datetime.fromisoformat(entry["sensorStartDate"].replace("Z", "+00:00"))
                return int(dt.timestamp() * 1000)
            if "sensorAge" in entry:
                return int(time.time() * 1000) - int(entry["sensorAge"]) * 60000
    except Exception as e:
        print(f"[warn] could not determine sensor start: {e}", file=sys.stderr)
    return None


def _get_json(url: str):
    req = Request(url)
    with urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def fetch_entries(count: int):
    url = f"{BASE_URL}/sgv.json?count={count}"
    return _get_json(url)


def post_entry(entry: dict) -> bool:
    """
    Matches the real /api/entries POST schema:
      {"type": "bg", "value": <float mmol/L>, "time": "<ISO8601>", "notes": "..."}
    Auth via x-api-key header.
    """
    ts_iso = datetime.fromtimestamp(entry["date"] / 1000, tz=timezone.utc).isoformat()
    payload = {
        "type": "bg",
        "value": mmol(entry["sgv"]),
        "time": ts_iso,
        "notes": "[CGM]",
    }
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        DIABET_LOGGER_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": DIABET_LOGGER_API_KEY,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return resp.status in (200, 201)
    except HTTPError as e:
        print(f"[error] POST failed ({e.code}): {e.read().decode()}", file=sys.stderr)
        return False
    except URLError as e:
        print(f"[error] POST failed: {e}", file=sys.stderr)
        return False


def main():
    _require_config()
    full_backfill = "--full" in sys.argv

    if full_backfill:
        sensor_start = fetch_sensor_start_ms()
        cutoff_ms = sensor_start if sensor_start else (int(time.time() * 1000) - 14 * 86400000)
        print(f"[info] full backfill requested, cutoff = {datetime.fromtimestamp(cutoff_ms/1000)}")
    else:
        cutoff_ms = load_watermark()
        print(f"[info] incremental pull, watermark = {datetime.fromtimestamp(cutoff_ms/1000) if cutoff_ms else 'none (will do 24h)'}")
        if cutoff_ms == 0:
            cutoff_ms = int(time.time() * 1000) - 24 * 3600 * 1000

    span_hours = max(1, (int(time.time() * 1000) - cutoff_ms) / 3600000)
    count = min(MAX_COUNT, max(1, int(span_hours * 60 * 1.2)))

    print(f"[info] requesting count={count} from Juggluco at {JUGGLUCO_HOST}:{JUGGLUCO_PORT}")
    try:
        data = fetch_entries(count)
    except Exception as e:
        print(f"[error] failed to fetch from Juggluco: {e}", file=sys.stderr)
        sys.exit(1)

    new_entries = [d for d in data if d.get("date", 0) > cutoff_ms]
    new_entries.sort(key=lambda d: d["date"])

    print(f"[info] {len(new_entries)} new entries to push (of {len(data)} fetched)")

    pushed = 0
    max_ts = cutoff_ms
    for entry in new_entries:
        if post_entry(entry):
            pushed += 1
            max_ts = max(max_ts, entry["date"])
        else:
            print(f"[warn] failed to push entry at {entry.get('dateString')}", file=sys.stderr)

    if pushed > 0:
        save_watermark(max_ts)

    print(f"[done] pushed {pushed}/{len(new_entries)} entries. watermark updated to {datetime.fromtimestamp(max_ts/1000)}")


if __name__ == "__main__":
    main()
