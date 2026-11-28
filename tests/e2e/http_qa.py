"""HTTP QA against the TEST instance (port 8766) — payloads mirror static/index.html + meal.html.
Reads test-only credentials from .env.test; never prints them."""
import json, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
import os
ENVF = sys.argv[2] if len(sys.argv) > 2 else ".env.test"
assert ":8766" in BASE or os.environ.get("ALLOW_LIVE") == "1", "live run needs ALLOW_LIVE=1"
env = dict(l.strip().split("=", 1) for l in open(ENVF) if "=" in l and not l.lstrip().startswith("#"))
env = {k.strip(): v.split(" #", 1)[0].strip().strip('"').strip("'") for k, v in env.items()}
CREATED = set()
KEY = env["DIABET_API_KEY"]
P = F = 0

def req(method, path, body=None, headers=None, raw=False):
    h = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode()
            return resp.status, (txt if raw else (json.loads(txt) if txt else None))
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try: return e.code, json.loads(txt)
        except Exception: return e.code, txt

def check(name, cond, info=""):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  →  {str(info)[:220]}")

H = {"X-API-Key": KEY}
WIN2030 = "/api/entries?from=2029-12-31&to=2030-01-03&limit=100000"
_s, _pre = req("GET", WIN2030, headers=H)
assert _s == 200 and _pre == [], f"2030 window not empty before run ({_s}, {len(_pre) if isinstance(_pre, list) else _pre}) — refusing (cleanup would delete it)"
base_t = datetime(2030, 1, 1, 8, 0, tzinfo=timezone.utc)
n = [0]
def ts():
    n[0] += 1
    return (base_t + timedelta(minutes=n[0])).isoformat().replace("+00:00", ".000Z")

print("== 1. Login (browser path)")
s, j = req("POST", "/api/login", {"username": env["DIABET_USERNAME"], "password": env["DIABET_PASSWORD"]})
tok = (j or {}).get("token") if isinstance(j, dict) else None
check("login → 200 + token не е null", s == 200 and isinstance(tok, str) and len(tok) > 20, f"{s} keys={list(j) if isinstance(j, dict) else j}")
HB = {"Authorization": "Bearer " + (tok or "")}

print("== 2. Всеки тип запис (браузърни payload-и) → 200")
cases = {
    "bg (глюкомер)":   {"type": "bg", "value": 6.7, "notes": "", "meta": {"source": "meter"}},
    "болус":           {"type": "fast", "value": 4.5, "notes": "", "carbs_g": 45, "meta": {"delivery": "pump", "insulin_type": "fiasp", "bolus_kind": "normal"}},
    "бавен инсулин":   {"type": "slow", "value": 14, "notes": "", "meta": {"delivery": "pen", "insulin_type": "tresiba"}},
    "кетони 0":        {"type": "ketones", "value": 0, "notes": ""},
    "кетони":          {"type": "ketones", "value": 0.3, "notes": ""},
    "активност":       {"type": "activity", "value": 30, "notes": "", "meta": {"intensity": "moderate"}},
    "алкохол":         {"type": "alcohol", "value": 1.5, "notes": ""},
    "смяна устройство":{"type": "device_change", "value": 0, "notes": "", "meta": {"device_type": "patch", "patch_filled_u": 200}},
    "хипо (meal)":     {"type": "meal", "value": 1.25, "carbs_g": 15, "notes": "[ХИПО] 3×5g"},
    "нощен кит":       {"type": "meal", "value": 2.5, "carbs_g": 30, "notes": "[ХИПО] Нощен: 15g бързи + 15g бавни"},
    "цигара":          {"type": "activity", "value": 5, "notes": "[ЦИГАРА] 1 бр.", "meta": {"intensity": "light"}},
    "период":          {"type": "device_change", "value": 0, "notes": "[ПЕРИОД] Болест (продължава)", "meta": {"device_type": "period", "period_type": "sick", "period_start": "2030-01-01", "period_end": None}},
    "темп базал":      {"type": "device_change", "value": 0, "notes": "Temp Basal 80% for 60 min", "meta": {"device_type": "settings_change", "temp_basal_pct": 80, "temp_basal_min": 60}},
    "бърз болус":      {"type": "fast", "value": 2, "notes": "manual bolus", "meta": {"delivery": "pump", "insulin_type": "fiasp", "bolus_kind": "normal"}},
    "спиране":         {"type": "device_change", "value": 0, "notes": "Temp Basal 0% for 30 min", "meta": {"device_type": "settings_change", "temp_basal_pct": 0, "temp_basal_min": 30}},
    "resume":          {"type": "device_change", "value": 0, "notes": "Resume basal", "meta": {"device_type": "settings_change", "resume": True}},
}
saved_ids = []
for name, p in cases.items():
    s, j = req("POST", "/api/entries", {**p, "time": ts()}, HB)   # browser uses Bearer
    check(f"{name} → 200", s == 200, f"{s} {j}")
    if s == 200: saved_ids.append(j["id"]); CREATED.add(j["id"])

print("== 3. meal.html — атомарен batch")
t = ts()
s, j = req("POST", "/api/entries/batch", {"entries": [
    {"type": "bg", "value": 7.2, "time": t, "notes": "преди: тест"},
    {"type": "meal", "value": 3, "time": t, "notes": "тест"},
    {"type": "fast", "value": 4, "time": t, "notes": "за 3 XE (авто-калк)"}]}, HB)
check("хранене КЗ+ХЕ+инсулин → 200, count=3", s == 200 and j.get("count") == 3, f"{s} {j}")
t = ts()
s, j = req("POST", "/api/entries/batch", {"entries": [
    {"type": "bg", "value": 6.5, "time": t, "notes": "преди вечерна доза"},
    {"type": "slow", "value": 14, "time": t, "notes": "вечерна доза"}]}, HB)
check("вечерна доза КЗ+бавен → 200, count=2", s == 200 and j.get("count") == 2, f"{s} {j}")
s, j = req("POST", "/api/calc-dose", {"bg": 12.5, "xe": 0}, HB)
check("калкулатор 0 XE → 200 (корекция)", s == 200, f"{s} {j}")
t = ts()
s, j = req("POST", "/api/entries/batch", {"entries": [
    {"type": "bg", "value": 12.5, "time": t, "notes": "корекция"},
    {"type": "fast", "value": 2, "time": t, "notes": "за корекция (авто-калк)"}]}, HB)
check("корекция 0 XE: КЗ + болус → 200, count=2", s == 200 and j.get("count") == 2, f"{s} {j}")
check("корекция 0 XE: няма meal ред", s == 200 and all(e["type"] != "meal" for e in j["entries"]), j)
if s == 200: saved_ids.extend(e["id"] for e in j["entries"])
before = len(req("GET", WIN2030, headers=H)[1] or [])
t = ts()
s, j = req("POST", "/api/entries/batch", {"entries": [
    {"type": "bg", "value": 7.2, "time": t}, {"type": "meal", "value": 0, "time": t}, {"type": "fast", "value": 4, "time": t}]}, HB)
after = len(req("GET", WIN2030, headers=H)[1] or [])
check("batch с ХЕ=0 → 422", s == 422, s)
check("batch 422 → нищо не е записано (няма частичен запис)", before == after, f"{before} → {after}")
check("batch 422 → четимо message", isinstance(j, dict) and "entries.1" in j.get("message", "") and "{" not in j.get("message", ""), j)

print("== 4. Невалиден вход → 422 с четимо съобщение")
bad = {
    "КЗ празна (null)": {"type": "bg", "value": None},
    "КЗ 0":             {"type": "bg", "value": 0},
    "КЗ 120 (mg/dL)":   {"type": "bg", "value": 120},
    "болус 0":          {"type": "fast", "value": 0},
    "болус carbs_g 0":  {"type": "fast", "value": 2, "carbs_g": 0},
    "кетони 11":        {"type": "ketones", "value": 11},
    "алкохол 20":       {"type": "alcohol", "value": 20},
    "активност 700":    {"type": "activity", "value": 700},
    "непознат тип":     {"type": "pizza", "value": 1},
}
for name, p in bad.items():
    s, j = req("POST", "/api/entries", {**p, "time": ts()}, HB)
    msg = j.get("message", "") if isinstance(j, dict) else ""
    ok = s == 422 and isinstance(j.get("detail"), list) and msg and "{" not in msg
    check(f"{name} → 422 | {msg}", ok, f"{s} {j}")
s, j = req("POST", "/api/entries", {"type": "bg", "value": 120, "time": ts()}, HB)
check("КЗ 120 → подсказка mg/dL = 6.7 mmol/L", "6.7 mmol/L" in j.get("message", ""), j)

print("== 5. Калкулатор: КЗ > 30 не дава доза")
s, j = req("POST", "/api/calc-dose", {"bg": 95, "xe": 3}, HB)
check("calc-dose bg=95 → 422", s == 422, f"{s} {j}")
s, j = req("POST", "/api/calc-dose", {"bg": 7.2, "xe": 3}, HB)
check("calc-dose bg=7.2 → 200", s == 200, f"{s} {j}")

print("== 6. История / отчет / export")
s, rows = req("GET", WIN2030, headers=H)
ids = {r["id"] for r in rows} if s == 200 else set()
check("новите записи са в историята", s == 200 and all(i in ids for i in saved_ids), f"{s} missing={[i for i in saved_ids if i not in ids][:5]}")
s, j = req("GET", "/api/report?from=2029-12-01&to=2030-01-31", headers=H)
check("отчет за лекар → 200", s == 200, f"{s} {str(j)[:200]}")
s, j = req("GET", "/api/export", headers=H)
exp_ids = set()
if s == 200:
    for v in (j.values() if isinstance(j, dict) else []):
        if isinstance(v, list):
            exp_ids |= {e.get("id") for e in v if isinstance(e, dict)}
check("export → 200 и съдържа новите записи", s == 200 and all(i in exp_ids for i in saved_ids), f"{s} {type(j)} {list(j)[:10] if isinstance(j, dict) else ''}")

print("== 6b. A1 дедуп / A2 обем")
t = ts()
s1, j1 = req("POST", "/api/entries", {"type": "bg", "value": 6.7, "time": t, "meta": {"source": "own"}}, HB)
s2, j2 = req("POST", "/api/entries", {"type": "bg", "value": 12.5, "time": t, "meta": {"source": "own"}}, HB)
check("A1: същата минута, друга стойност → нов ред", s2 == 200 and j2.get("duplicate") is None and j2["id"] != j1["id"], j2)
s3, j3 = req("POST", "/api/entries", {"type": "bg", "value": 12.5, "time": t, "meta": {"source": "own"}}, HB)
check("A1: идентичен повтор → duplicate, същия id", j3.get("duplicate") is True and j3["id"] == j2["id"], j3)
s, rows = req("GET", "/api/entries", headers=H)
check(f"A2: GET без limit → всички ({len(rows) if isinstance(rows, list) else '?'} реда, > 20000)", s == 200 and len(rows) > 20000, s)

print("== 7. Статиката в image-а е новата")
s, js = req("GET", "/static/i18n.js", raw=True)
check("i18n.js съдържа apiErrorText + bgMgdlGuard", s == 200 and "apiErrorText" in js and "bgMgdlGuard" in js)
for lang in ("bg", "en", "de", "pt-BR"):
    s, d = req("GET", f"/static/lang/{lang}.json")
    check(f"lang/{lang}.json има новите 4 ключа", s == 200 and all(k in d for k in ("toast_value_positive", "toast_value_range", "confirm_bg_mgdl", "hint_bg_mgdl")))
s, html = req("GET", "/static/meal.html", raw=True)
check("meal.html ползва /entries/batch", s == 200 and html.count("/entries/batch") == 2)

# cleanup: everything in the 2030 window this script writes to
s, rows = req("GET", WIN2030, headers=H)
for r in rows: req("DELETE", f"/api/entries/{r['id']}", headers=H)
s, rows = req("GET", WIN2030, headers=H)
check(f"cleanup: 2030 тестов прозорец празен", s == 200 and rows == [], rows[:3] if isinstance(rows, list) else rows)
print(f"\nHTTP QA: {P} passed, {F} failed")
sys.exit(1 if F else 0)
