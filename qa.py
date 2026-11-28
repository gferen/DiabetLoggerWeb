"""
Пълен QA на Diabet Logger v3.
Проверява: калкулации, edge cases, auth варианти, миграция, data integrity.
"""
import os
import sys
import json
import sqlite3
import tempfile
import shutil
from datetime import datetime, timedelta, timezone

# Изолирана тестова среда
TEST_DIR = tempfile.mkdtemp(prefix="diabet_qa_")
os.environ['DIABET_DB'] = f'{TEST_DIR}/test.db'
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt_secret'

# app.py е до този файл — без hardcoded път, работи и локално, и в контейнера
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ========== ТЕСТОВА ИНФРАСТРУКТУРА ==========
PASSED = 0
FAILED = 0
FAILURES = []

def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  ✗ {name}  →  {detail}")

def section(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")


# ========== 1. КАЛКУЛАЦИИ ==========
section("1. КАЛКУЛАЦИИ (unit tests, независими от API)")

# 1.1 GMI формула
# GMI(%) = 3.31 + 0.02392 × mean_mg_dL
# mg/dL = mmol/L × 18.018
# Reference: Bergenstal et al. 2018, "Glucose Management Indicator (GMI)"
def calc_gmi(mean_mmol):
    return 3.31 + 0.02392 * (mean_mmol * 18.018)

# Известни двойки от Bergenstal 2018 Table 1:
# mean glucose 154 mg/dL → GMI 7.0% (approximately)
# 154 mg/dL = 8.55 mmol/L
gmi_test = calc_gmi(8.55)
check("GMI @ 8.55 mmol/L ≈ 7.0%", abs(gmi_test - 7.0) < 0.1, f"got {gmi_test:.2f}")

# mean 183 mg/dL = 10.16 mmol/L → GMI ~7.7%
gmi_test = calc_gmi(10.16)
check("GMI @ 10.16 mmol/L ≈ 7.7%", abs(gmi_test - 7.69) < 0.05, f"got {gmi_test:.2f}")

# mean 126 mg/dL = 6.99 mmol/L → GMI ~6.3%
gmi_test = calc_gmi(6.99)
check("GMI @ 6.99 mmol/L ≈ 6.3%", abs(gmi_test - 6.32) < 0.05, f"got {gmi_test:.2f}")

# Ниска граница: mean 5.0 mmol/L → GMI ~5.5%
gmi_test = calc_gmi(5.0)
check("GMI @ 5.0 mmol/L ≈ 5.46%", abs(gmi_test - 5.46) < 0.05, f"got {gmi_test:.2f}")


# 1.2 TIR bands boundaries
# Battelino et al. 2019: <3.0 / 3.0-3.8 / 3.9-10.0 / 10.1-13.9 / >13.9 mmol/L
# Провери гранични стойности
def classify_bg(v):
    if v < 3.0: return "very_low"
    if v < 3.9: return "low"
    if v <= 10.0: return "in_range"
    if v <= 13.9: return "high"
    return "very_high"

check("2.9 → very_low", classify_bg(2.9) == "very_low")
check("3.0 → low (граница)", classify_bg(3.0) == "low")
check("3.8 → low", classify_bg(3.8) == "low")
check("3.89 → low", classify_bg(3.89) == "low")
check("3.9 → in_range (граница)", classify_bg(3.9) == "in_range")
check("10.0 → in_range (граница)", classify_bg(10.0) == "in_range")
check("10.1 → high", classify_bg(10.1) == "high")
check("13.9 → high (граница)", classify_bg(13.9) == "high")
check("14.0 → very_high", classify_bg(14.0) == "very_high")
check("25.0 → very_high", classify_bg(25.0) == "very_high")


# 1.3 SD и CV изчисления (population SD, N в знаменателя, не N-1)
def calc_stats(values):
    n = len(values)
    if n == 0:
        return None, None, None
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n
    sd = var ** 0.5
    cv = (sd / mean) * 100 if mean else 0
    return mean, sd, cv

# Известни: [4, 8, 6, 5, 3, 7] → mean=5.5, SD(pop)=1.708, CV=31.1%
mean, sd, cv = calc_stats([4, 8, 6, 5, 3, 7])
check("mean([4,8,6,5,3,7]) = 5.5", abs(mean - 5.5) < 0.001, f"got {mean}")
check("SD(pop) ≈ 1.708", abs(sd - 1.7078) < 0.01, f"got {sd:.4f}")
check("CV ≈ 31.05%", abs(cv - 31.05) < 0.1, f"got {cv:.2f}")

# Един елемент → SD=0
mean, sd, cv = calc_stats([7.0])
check("Single value SD = 0", sd == 0)
check("Single value CV = 0", cv == 0)


# ========== 2. API END-TO-END ==========
section("2. API END-TO-END (изолирана DB)")

# Fresh env
os.environ['DIABET_USERNAME'] = 'test'
os.environ['DIABET_PASSWORD'] = 'testpass123'
os.environ['DIABET_API_KEY'] = 'apikey-abc'

# Import fresh
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_mod
from fastapi.testclient import TestClient
client = TestClient(app_mod.app)

# 2.1 Health
r = client.get('/api/health')
check("GET /api/health → 200", r.status_code == 200)
check("Health: login enabled", r.json()['auth']['login'] is True)
check("Health: api_key enabled", r.json()['auth']['api_key'] is True)

# 2.2 Login
r = client.post('/api/login', json={'username': 'test', 'password': 'testpass123'})
check("Login с правилни credentials → 200", r.status_code == 200)
check("Login връща token", 'token' in r.json())
check("Login връща expires_at", 'expires_at' in r.json())
token = r.json()['token']

r = client.post('/api/login', json={'username': 'test', 'password': 'wrong'})
check("Login с грешна парола → 401", r.status_code == 401)

r = client.post('/api/login', json={'username': 'wrong', 'password': 'testpass123'})
check("Login с грешен потребител → 401", r.status_code == 401)

# 2.3 Auth варианти
r = client.get('/api/entries')
check("No auth → 401", r.status_code == 401)

r = client.get('/api/entries', headers={'X-API-Key': 'wrong'})
check("Wrong API key → 401", r.status_code == 401)

r = client.get('/api/entries', headers={'X-API-Key': 'apikey-abc'})
check("Correct API key → 200", r.status_code == 200)

r = client.get('/api/entries', headers={'Authorization': 'Bearer wrong'})
check("Wrong JWT → 401", r.status_code == 401)

r = client.get('/api/entries', headers={'Authorization': f'Bearer {token}'})
check("Correct JWT → 200", r.status_code == 200)

# Malformed Authorization header
r = client.get('/api/entries', headers={'Authorization': f'{token}'})  # no "Bearer"
check("Missing 'Bearer' prefix → 401", r.status_code == 401)

r = client.get('/api/entries', headers={'Authorization': 'Bearer '})  # empty token
check("Empty Bearer token → 401", r.status_code == 401)

H = {'X-API-Key': 'apikey-abc'}


# ========== 3. ВАЛИДАЦИЯ НА ВХОДА ==========
section("3. ВАЛИДАЦИЯ НА ВХОДА")

# Invalid type
r = client.post('/api/entries', json={'type': 'invalid', 'value': 5, 'time': '2026-07-01T10:00:00'}, headers=H)
check("Невалиден type → 422", r.status_code == 422)

# Missing fields
r = client.post('/api/entries', json={'type': 'bg', 'value': 5}, headers=H)
check("Липсва time → 422", r.status_code == 422)

r = client.post('/api/entries', json={'type': 'bg', 'time': '2026-07-01T10:00:00'}, headers=H)
check("Липсва value → 422", r.status_code == 422)

# Value bounds (VALUE_RANGES: bg 1–30 mmol/L)
r = client.post('/api/entries', json={'type': 'bg', 'value': 0, 'time': '2026-07-01T10:00:00'}, headers=H)
check("value=0 → 422", r.status_code == 422)

r = client.post('/api/entries', json={'type': 'bg', 'value': -5, 'time': '2026-07-01T10:00:00'}, headers=H)
check("Отрицателна value → 422", r.status_code == 422)

r = client.post('/api/entries', json={'type': 'bg', 'value': 100, 'time': '2026-07-01T10:00:00'}, headers=H)
check("value=100 → 422", r.status_code == 422)

r = client.post('/api/entries', json={'type': 'bg', 'value': 30.1, 'time': '2026-07-01T10:00:00'}, headers=H)
check("bg=30.1 → 422 (max 30 mmol/L)", r.status_code == 422)

r = client.post('/api/entries', json={'type': 'bg', 'value': 30, 'time': '2026-07-01T10:00:00'}, headers=H)
check("bg=30 → 200 (граница)", r.status_code == 200)
# Изтрий го веднага, за да не замърсява тестовете
client.delete(f"/api/entries/{r.json()['id']}", headers=H)

# Invalid time
r = client.post('/api/entries', json={'type': 'bg', 'value': 5, 'time': 'not-a-date'}, headers=H)
check("Невалидна дата → 400", r.status_code == 400)

r = client.post('/api/entries', json={'type': 'bg', 'value': 5, 'time': ''}, headers=H)
check("Празна дата → 400", r.status_code == 400)

# Валидни варианти на време
for ts in ['2026-07-01T10:00:00', '2026-07-01T10:00:00Z', '2026-07-01T10:00:00+02:00', '2026-07-01T10:00:00.123456']:
    r = client.post('/api/entries', json={'type': 'bg', 'value': 5.5, 'time': ts, 'notes': f'time-fmt {ts}'}, headers=H)
    check(f"ISO време '{ts}' → 200", r.status_code == 200)
    if r.status_code == 200:
        client.delete(f"/api/entries/{r.json()['id']}", headers=H)


# ========== 4. TIR/GMI/CV С РЕАЛНИ ДАННИ ==========
section("4. TIR/GMI/CV — верифицирани сценарии")

# Clean slate
client.delete('/api/entries', headers=H)

# Известен сценарий: 10 стойности, разпределени в bands
now = datetime.now(timezone.utc)
scenarios = [
    (2.5, "very_low"),
    (3.5, "low"),
    (3.7, "low"),
    (5.0, "in_range"),
    (6.5, "in_range"),
    (7.0, "in_range"),
    (8.5, "in_range"),
    (9.9, "in_range"),
    (11.5, "high"),
    (15.0, "very_high"),
]
# 10 стойности → 1 very_low (10%), 2 low (20%), 5 in_range (50%), 1 high (10%), 1 very_high (10%)
for i, (v, _) in enumerate(scenarios):
    t = (now - timedelta(hours=i)).isoformat()
    r = client.post('/api/entries', json={'type': 'bg', 'value': v, 'time': t}, headers=H)
    assert r.status_code == 200

r = client.get('/api/stats?days=1', headers=H)
s = r.json()

check("bg_count = 10", s['bg_count'] == 10, f"got {s['bg_count']}")
check("TIR very_low = 10%", s['tir_pct']['very_low'] == 10.0, f"got {s['tir_pct']['very_low']}")
check("TIR low = 20%", s['tir_pct']['low'] == 20.0, f"got {s['tir_pct']['low']}")
check("TIR in_range = 50%", s['tir_pct']['in_range'] == 50.0, f"got {s['tir_pct']['in_range']}")
check("TIR high = 10%", s['tir_pct']['high'] == 10.0, f"got {s['tir_pct']['high']}")
check("TIR very_high = 10%", s['tir_pct']['very_high'] == 10.0, f"got {s['tir_pct']['very_high']}")
check("TIR sum = 100%", abs(sum(s['tir_pct'].values()) - 100.0) < 0.01)

# Mean = (2.5+3.5+3.7+5+6.5+7+8.5+9.9+11.5+15)/10 = 73.1/10 = 7.31
expected_mean = sum(v for v, _ in scenarios) / len(scenarios)
check(f"mean_bg = {expected_mean:.2f}", abs(s['mean_bg'] - expected_mean) < 0.01, f"got {s['mean_bg']}")

# GMI: 3.31 + 0.02392 × 7.31 × 18.018
expected_gmi = 3.31 + 0.02392 * expected_mean * 18.018
check(f"GMI = {expected_gmi:.2f}%", abs(s['gmi_pct'] - round(expected_gmi, 2)) < 0.01, f"got {s['gmi_pct']}")


# ========== 5. EDGE CASES ==========
section("5. EDGE CASES")

# 5.1 Empty stats
client.delete('/api/entries', headers=H)
r = client.get('/api/stats?days=14', headers=H)
s = r.json()
check("Empty stats: bg_count = 0", s['bg_count'] == 0)
check("Empty stats: mean_bg = None", s['mean_bg'] is None)
check("Empty stats: gmi_pct = None", s['gmi_pct'] is None)
check("Empty stats: TIR all zero", all(v == 0 for v in s['tir_pct'].values()))
check("Empty stats: insulin all 0", s['insulin_total']['fast'] == 0)

# 5.2 Boundary values на самите TIR граници
now = datetime.now(timezone.utc)
boundaries = [3.0, 3.9, 10.0, 13.9]  # трябва да отиват съответно в low, in_range, in_range, high
expected_bands = ["low", "in_range", "in_range", "high"]
for i, v in enumerate(boundaries):
    client.post('/api/entries', json={'type': 'bg', 'value': v, 'time': (now - timedelta(minutes=i)).isoformat()}, headers=H)

r = client.get('/api/stats?days=1', headers=H)
s = r.json()
check("Boundary: low count = 1 (v=3.0)", s['tir_bands']['low'] == 1)
check("Boundary: in_range count = 2 (v=3.9,10.0)", s['tir_bands']['in_range'] == 2)
check("Boundary: high count = 1 (v=13.9)", s['tir_bands']['high'] == 1)

# 5.3 Stats с days=0 (защита срещу деление на нула)
r = client.get('/api/stats?days=0', headers=H)
check("days=0 → 200 (защитен)", r.status_code == 200)

# 5.4 Stats с отрицателни дни
r = client.get('/api/stats?days=-5', headers=H)
check("days=-5 → 200 (нормализира на 1)", r.status_code == 200)

# 5.5 Много стар запис - не трябва да е в stats period
client.delete('/api/entries', headers=H)
old = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
client.post('/api/entries', json={'type': 'bg', 'value': 25.0, 'time': old}, headers=H)
r = client.get('/api/stats?days=7', headers=H)
check("Old record (365d) не се брои в days=7 stats", r.json()['bg_count'] == 0)
r = client.get('/api/stats?days=400', headers=H)
check("Old record се брои в days=400 stats", r.json()['bg_count'] == 1)

# 5.6 Delete на несъществуващ ID
r = client.delete('/api/entries/99999', headers=H)
check("DELETE несъществуващ ID → 404", r.status_code == 404)


# ========== 6. IMPORT/EXPORT ==========
section("6. IMPORT / EXPORT")

client.delete('/api/entries', headers=H)

# Import
payload = {
    "bg": [
        {"value": 7.5, "time": "2026-06-01T10:00:00", "notes": "test1"},
        {"value": 6.0, "time": "2026-06-01T14:00:00", "notes": "test2"},
    ],
    "fast": [{"units": 8, "time": "2026-06-01T10:05:00", "notes": ""}],
    "slow": [{"units": 20, "time": "2026-06-01T22:00:00", "notes": ""}],
    "ketones": [{"value": 0.3, "time": "2026-06-01T08:00:00", "notes": ""}],
    "replace": False
}
r = client.post('/api/import', json=payload, headers=H)
check("Import merge → 200", r.status_code == 200)
check("Import: imported=5", r.json()['imported'] == 5)

# Verify data
entries = client.get('/api/entries', headers=H).json()
check("После import: 5 записа", len(entries) == 5)
by_type = {}
for e in entries:
    by_type[e['type']] = by_type.get(e['type'], 0) + 1
check("Import: 2 bg", by_type.get('bg') == 2)
check("Import: 1 fast", by_type.get('fast') == 1)
check("Import: 1 slow", by_type.get('slow') == 1)
check("Import: 1 ketones", by_type.get('ketones') == 1)

# Import merge (не replace) — повторният import на същия файл е no-op.
# UNIQUE(type,time) прави merge-а идемпотентен: преди този fix същият файл,
# импортиран два пъти, дублираше всички записи и изкривяваше TIR/mean/GMI.
r = client.post('/api/import', json=payload, headers=H)
check("Повторен merge → 200", r.status_code == 200)
check("Повторен merge: imported=0", r.json()['imported'] == 0, f"got {r.json()}")
check("Повторен merge: skipped=5", r.json()['skipped'] == 5, f"got {r.json()}")
entries = client.get('/api/entries', headers=H).json()
check("Повторен merge → пак 5 записа (без дубликати)", len(entries) == 5, f"got {len(entries)}")

# Import replace
payload['replace'] = True
payload['bg'] = [{"value": 5.5, "time": "2026-06-02T10:00:00", "notes": "only"}]
payload['fast'] = payload['slow'] = payload['ketones'] = []
r = client.post('/api/import', json=payload, headers=H)
check("Import replace → 200", r.status_code == 200)
entries = client.get('/api/entries', headers=H).json()
check("След replace: 1 запис", len(entries) == 1)

# Import с невалидни записи (missing time / missing value) - пропускат се
payload_bad = {
    "bg": [
        {"value": 7.0, "time": "2026-06-03T10:00:00"},  # ok
        {"value": 7.0},  # no time - skip
        {"time": "2026-06-03T11:00:00"},  # no value - skip
        {"value": 7.0, "time": "bad-date"},  # bad time - skip
    ],
    "replace": True
}
r = client.post('/api/import', json=payload_bad, headers=H)
check("Import с mixed valid/invalid → 200", r.status_code == 200)
check("Import: само 1 валиден вкаран", r.json()['imported'] == 1)

# Export round-trip
r = client.get('/api/export', headers=H)
exp = r.json()
check("Export връща всичките 4 категории", set(exp.keys()) >= {'bg', 'fast', 'slow', 'ketones'})

# Empty export
client.delete('/api/entries', headers=H)
r = client.get('/api/export', headers=H)
check("Empty export: bg=[]", r.json()['bg'] == [])


# ========== 7. МИГРАЦИЯ v2 → v3 ==========
section("7. МИГРАЦИЯ v2 → v3")

# Създай v2 DB
v2_path = f'{TEST_DIR}/v2.db'
conn = sqlite3.connect(v2_path)
conn.execute("""
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL CHECK(type IN ('bg','fast','slow')),
        value REAL NOT NULL,
        time TEXT NOT NULL,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
""")
for i in range(50):
    conn.execute("INSERT INTO entries (type,value,time,notes) VALUES (?,?,?,?)",
                 ("bg", 7.0 + i*0.1, f"2026-06-{(i%28)+1:02d}T10:00:00", f"v2 #{i}"))
conn.commit()
conn.close()

# Провери оригиналната схема
conn = sqlite3.connect(v2_path)
row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'").fetchone()
check("Pre-migration: CHECK constraint съществува", "CHECK" in row[0])
check("Pre-migration: 'ketones' НЕ е в CHECK", "ketones" not in row[0])
count_before = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
conn.close()

# Изпълни миграция
os.environ['DIABET_DB'] = v2_path
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt_secret2'
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_v3
client2 = TestClient(app_v3.app)

# Провери резултата
conn = sqlite3.connect(v2_path)
row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'").fetchone()
check("Post-migration: CHECK constraint премахнат", "CHECK" not in (row[0] or ""))
count_after = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
check(f"Post-migration: данните запазени ({count_before}→{count_after})", count_after == count_before)
old_ids = [r[0] for r in conn.execute("SELECT id FROM entries ORDER BY id").fetchall()]
check("Post-migration: ID-та запазени", old_ids == list(range(1, 51)))
conn.close()

# Можем ли да добавим ketones?
r = client2.post('/api/entries',
                 json={'type': 'ketones', 'value': 0.5, 'time': '2026-07-01T08:00:00'},
                 headers=H)
check("Post-migration: ketones приема се", r.status_code == 200)

# Повторно стартиране върху вече мигрирана DB (idempotent?)
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_v3_second
client3 = TestClient(app_v3_second.app)
count_final = client3.get('/api/entries', headers=H).json()
check("Повторна миграция: идемпотентна", len(count_final) == count_before + 1)


# ========== 8. АВТОРИЗАЦИОННИ КОМБИНАЦИИ ==========
section("8. AUTH КОМБИНАЦИИ")

# 8.1 Само API key
os.environ['DIABET_USERNAME'] = ''
os.environ['DIABET_PASSWORD'] = ''
os.environ['DIABET_API_KEY'] = 'onlykey'
os.environ['DIABET_DB'] = f'{TEST_DIR}/auth1.db'
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt3'
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_a
c_a = TestClient(app_a.app)

h = c_a.get('/api/health').json()
check("Само API key: login=False, api_key=True", h['auth']['login'] is False and h['auth']['api_key'] is True)
check("Само API key: /login → 400", c_a.post('/api/login', json={'username':'x','password':'y'}).status_code == 400)
check("Само API key: без auth → 401", c_a.get('/api/entries').status_code == 401)
check("Само API key: с key → 200", c_a.get('/api/entries', headers={'X-API-Key':'onlykey'}).status_code == 200)

# 8.2 Само login
os.environ['DIABET_USERNAME'] = 'onlyuser'
os.environ['DIABET_PASSWORD'] = 'onlypass'
os.environ['DIABET_API_KEY'] = ''
os.environ['DIABET_DB'] = f'{TEST_DIR}/auth2.db'
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt4'
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_b
c_b = TestClient(app_b.app)

h = c_b.get('/api/health').json()
check("Само login: login=True, api_key=False", h['auth']['login'] is True and h['auth']['api_key'] is False)
r = c_b.post('/api/login', json={'username':'onlyuser','password':'onlypass'})
check("Само login: успешен login", r.status_code == 200)
tok_b = r.json()['token']
check("Само login: API key не работи", c_b.get('/api/entries', headers={'X-API-Key':'anything'}).status_code == 401)
check("Само login: с token → 200", c_b.get('/api/entries', headers={'Authorization':f'Bearer {tok_b}'}).status_code == 200)

# 8.3 Без auth (open mode)
os.environ['DIABET_USERNAME'] = ''
os.environ['DIABET_PASSWORD'] = ''
os.environ['DIABET_API_KEY'] = ''
os.environ['DIABET_DB'] = f'{TEST_DIR}/auth3.db'
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt5'
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_c
c_c = TestClient(app_c.app)

h = c_c.get('/api/health').json()
check("Open mode: и двете False", not h['auth']['login'] and not h['auth']['api_key'])
check("Open mode: без auth → 200", c_c.get('/api/entries').status_code == 200)

# 8.4 Timing attack защита (constant-time compare)
import hmac as _hmac
# Не можем директно да измерим timing, но проверяваме че се използва compare_digest
import app as app_check
import inspect
src = inspect.getsource(app_check)
check("Login: използва hmac.compare_digest", "hmac.compare_digest" in src)


# ========== 9. JWT TOKEN VALIDATION ==========
section("9. JWT TOKENS")

os.environ['DIABET_USERNAME'] = 'jwtuser'
os.environ['DIABET_PASSWORD'] = 'jwtpass'
os.environ['DIABET_API_KEY'] = ''
os.environ['DIABET_DB'] = f'{TEST_DIR}/jwt.db'
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt6'
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_j
c_j = TestClient(app_j.app)

r = c_j.post('/api/login', json={'username':'jwtuser','password':'jwtpass'})
tok = r.json()['token']

# Валиден token
check("Валиден JWT → 200", c_j.get('/api/entries', headers={'Authorization':f'Bearer {tok}'}).status_code == 200)

# Модифициран token (payload change)
import jwt as pyjwt
parts = tok.split('.')
# ВАЖНО: не пипай ПОСЛЕДНИЯ символ на подписа — при 32-байтов HMAC той носи само
# 4 значещи бита, така че ~1 от 4 замени декодира до същите байтове и подписът
# остава валиден (тестът беше flaky заради това). Първият символ носи 6 бита.
sig = parts[2]
tampered_sig = ('a' if sig[0] != 'a' else 'b') + sig[1:]
tampered = f"{parts[0]}.{parts[1]}.{tampered_sig}"
check("Tampered JWT → 401", c_j.get('/api/entries', headers={'Authorization':f'Bearer {tampered}'}).status_code == 401)

# Изтекъл token
expired = pyjwt.encode(
    {"sub": "jwtuser", "exp": datetime.now(timezone.utc) - timedelta(days=1)},
    app_j.JWT_SECRET,
    algorithm="HS256"
)
check("Expired JWT → 401", c_j.get('/api/entries', headers={'Authorization':f'Bearer {expired}'}).status_code == 401)

# Token с грешен secret
wrong_secret = pyjwt.encode(
    {"sub": "jwtuser", "exp": datetime.now(timezone.utc) + timedelta(days=1)},
    "wrong-secret-xyz",
    algorithm="HS256"
)
check("Wrong-secret JWT → 401", c_j.get('/api/entries', headers={'Authorization':f'Bearer {wrong_secret}'}).status_code == 401)


# ========== 10. DATA INTEGRITY ==========
section("10. DATA INTEGRITY")

os.environ['DIABET_API_KEY'] = 'intkey'
os.environ['DIABET_DB'] = f'{TEST_DIR}/int.db'
os.environ['DIABET_SECRET_FILE'] = f'{TEST_DIR}/.jwt7'
os.environ['DIABET_USERNAME'] = ''
os.environ['DIABET_PASSWORD'] = ''
if 'app' in sys.modules:
    del sys.modules['app']
import app as app_i
c_i = TestClient(app_i.app)
H2 = {'X-API-Key': 'intkey'}

# Add many, delete one, verify rest intact
ids = []
for i in range(20):
    r = c_i.post('/api/entries', json={'type':'bg','value':5+i*0.1,'time':f'2026-06-{(i%28)+1:02d}T10:00:00'}, headers=H2)
    ids.append(r.json()['id'])

# Delete middle
c_i.delete(f'/api/entries/{ids[10]}', headers=H2)
remaining = c_i.get('/api/entries', headers=H2).json()
check("След delete: 19 записа", len(remaining) == 19)
remaining_ids = {e['id'] for e in remaining}
check("Изтритият ID го няма", ids[10] not in remaining_ids)
check("Всички други ID-та ги има", set(ids) - {ids[10]} == remaining_ids)

# Notes с кирилица
r = c_i.post('/api/entries', json={'type':'bg','value':7.0,'time':'2026-07-01T10:00:00','notes':'преди закуска, чувствам се уморен 😴'}, headers=H2)
check("Кирилица + emoji в notes → 200", r.status_code == 200)
back = c_i.get('/api/entries?type=bg', headers=H2).json()
found = next((e for e in back if e['id'] == r.json()['id']), None)
check("Кирилица + emoji се възстановяват", found and 'преди закуска' in found['notes'] and '😴' in found['notes'])

# SQL injection опит
r = c_i.post('/api/entries', json={'type':'bg','value':5,'time':'2026-07-01T10:00:00','notes':"'; DROP TABLE entries; --"}, headers=H2)
check("SQL injection в notes → 200 (parameterized query)", r.status_code == 200)
# Ако беше уязвимо, следващият call щеше да е 500
r = c_i.get('/api/entries', headers=H2)
check("След SQL injection опит: таблицата стои", r.status_code == 200 and len(r.json()) > 0)

# Time с timezone → нормализира се
r = c_i.post('/api/entries', json={'type':'bg','value':5,'time':'2026-07-01T10:00:00+03:00'}, headers=H2)
back_time = next(e for e in c_i.get('/api/entries', headers=H2).json() if e['id'] == r.json()['id'])['time']
parsed = datetime.fromisoformat(back_time)
check(f"Timezone-aware време преживява round-trip", parsed is not None)


# ========== 11. ЛОКАЛЕН ДЕН + ДЕДУПЛИКАЦИЯ ==========
section("11. ЛОКАЛЕН ДЕН (UTC+3) + ДЕДУПЛИКАЦИЯ")

import app as app_module

# --- local_day() unit tests (Europe/Sofia = UTC+3 през лятото) ---
check("local_day: 01:30 местно (22:30 UTC пред. ден) → правилният ден",
      app_module.local_day("2026-07-15T22:30:00+00:00") == "2026-07-16",
      f"got {app_module.local_day('2026-07-15T22:30:00+00:00')}")
check("local_day: 23:00 местно (20:00 UTC) → същият ден",
      app_module.local_day("2026-07-15T20:00:00+00:00") == "2026-07-15")
check("local_day: naive timestamp се приема за UTC",
      app_module.local_day("2026-07-15T22:30:00") == "2026-07-16")
check("local_day: 'Z' суфикс се обработва",
      app_module.local_day("2026-07-15T22:30:00Z") == "2026-07-16")
check("local_day: невалиден вход не хвърля",
      app_module.local_day("боза") == "боза"[:10])
check("/api/health отдава активната timezone",
      "timezone" in c_i.get('/api/health').json())

# --- /api/report daily grouping: запис в 01:00 местно време ---
c_i.delete('/api/entries', headers=H2)
# 2026-07-15T22:30:00Z == 2026-07-16 01:30 местно време
c_i.post('/api/entries', json={'type':'bg','value':7.0,'time':'2026-07-15T22:30:00+00:00'}, headers=H2)
c_i.post('/api/entries', json={'type':'fast','value':6.0,'time':'2026-07-15T22:31:00+00:00'}, headers=H2)
rep = c_i.get('/api/report?from=2026-07-14&to=2026-07-17', headers=H2).json()
days = {d['date'] for d in rep['daily']}
check("report: нощният запис пада на 16-ти (локално), не на 15-ти",
      days == {'2026-07-16'}, f"got {days}")
check("report: fast_active_days = 1 (не се разцепва на два дни)",
      rep['insulin']['fast_active_days'] == 1, f"got {rep['insulin']['fast_active_days']}")

# --- дедупликация на (type, time) ---
c_i.delete('/api/entries', headers=H2)
r1 = c_i.post('/api/entries', json={'type':'bg','value':5.5,'time':'2026-07-20T08:00:00+00:00','notes':'[CGM]'}, headers=H2)
r2 = c_i.post('/api/entries', json={'type':'bg','value':5.5,'time':'2026-07-20T08:00:00+00:00','notes':'[CGM]'}, headers=H2)
check("CGM resend → 200, не 500", r2.status_code == 200, f"got {r2.status_code}")
check("CGM resend е маркиран duplicate", r2.json().get('duplicate') is True, f"got {r2.json()}")
check("CGM resend връща същия id", r2.json()['id'] == r1.json()['id'])
check("CGM resend не създава втори запис",
      len(c_i.get('/api/entries', headers=H2).json()) == 1)
# различен тип на същия timestamp е легитимен (доза + BG заедно)
r3 = c_i.post('/api/entries', json={'type':'fast','value':4,'time':'2026-07-20T08:00:00+00:00'}, headers=H2)
check("Различен тип на същия timestamp минава",
      r3.status_code == 200 and r3.json().get('duplicate') is None)
check("След това: 2 записа", len(c_i.get('/api/entries', headers=H2).json()) == 2)


section("12. STRUCTURED ДАННИ — НОВИ ТИПОВЕ И ПОЛЕТА")

client.delete('/api/entries', headers=H)

# --- нови типове минават валидацията ---
r = client.post('/api/entries', json={'type':'activity','value':30,'time':'2026-08-01T09:00:00+00:00','meta':{'intensity':'moderate'}}, headers=H)
check("activity → 200", r.status_code == 200, f"got {r.status_code}")
check("activity: meta се връща обратно", r.json().get('meta') == {'intensity':'moderate'})

r = client.post('/api/entries', json={'type':'alcohol','value':2.0,'time':'2026-08-01T20:00:00+00:00'}, headers=H)
check("alcohol → 200", r.status_code == 200)

# --- device_change: единственият тип, при който value=0 е позволено ---
r = client.post('/api/entries', json={'type':'device_change','value':0,'time':'2026-08-02T08:00:00+00:00','meta':{'device_type':'sensor'}}, headers=H)
check("device_change value=0 → 200", r.status_code == 200, f"got {r.status_code}")
check("device_change: meta.device_type се връща обратно", r.json().get('meta') == {'device_type':'sensor'})

# --- останалите типове ПАЗЯТ старото поведение: value=0 → 422 ---
r = client.post('/api/entries', json={'type':'activity','value':0,'time':'2026-08-02T09:00:00+00:00'}, headers=H)
check("activity value=0 → 422 (не device_change)", r.status_code == 422, f"got {r.status_code}")

r = client.post('/api/entries', json={'type':'alcohol','value':0,'time':'2026-08-02T10:00:00+00:00'}, headers=H)
check("alcohol value=0 → 422 (не device_change)", r.status_code == 422, f"got {r.status_code}")

# --- meal_time и carbs_g на 'fast' запис (разделен dose/meal timestamp) ---
r = client.post('/api/entries', json={
    'type':'fast','value':8,'time':'2026-08-03T12:15:00+00:00',
    'meal_time':'2026-08-03T12:00:00+00:00','carbs_g':45.0
}, headers=H)
check("fast с meal_time+carbs_g → 200", r.status_code == 200)
body = r.json()
check("meal_time се връща коректно", body.get('meal_time') == '2026-08-03T12:00:00+00:00')
check("carbs_g се връща коректно", body.get('carbs_g') == 45.0)

# невалиден meal_time формат → 400
r = client.post('/api/entries', json={'type':'fast','value':8,'time':'2026-08-03T13:00:00+00:00','meal_time':'not-a-date'}, headers=H)
check("невалиден meal_time → 400", r.status_code == 400, f"got {r.status_code}")

# --- GET /api/entries връща новите полета ---
entries = client.get('/api/entries', headers=H).json()
fast_entry = next(e for e in entries if e['type'] == 'fast')
check("GET /entries включва meal_time", fast_entry.get('meal_time') == '2026-08-03T12:00:00+00:00')
check("GET /entries включва carbs_g", fast_entry.get('carbs_g') == 45.0)
activity_entry = next(e for e in entries if e['type'] == 'activity')
check("GET /entries включва meta (dict, не JSON string)", activity_entry.get('meta') == {'intensity':'moderate'})

# --- export/import round-trip за новите типове ---
exported = client.get('/api/export', headers=H).json()
check("export включва activity ключ", 'activity' in exported and len(exported['activity']) == 1)
check("export включва alcohol ключ", 'alcohol' in exported and len(exported['alcohol']) == 1)
check("export включва device_change ключ", 'device_change' in exported and len(exported['device_change']) == 1)
check("export на fast носи meal_time/carbs_g",
      exported['fast'][0].get('meal_time') == '2026-08-03T12:00:00+00:00' and exported['fast'][0].get('carbs_g') == 45.0)

client.delete('/api/entries', headers=H)
r = client.post('/api/import', json=exported, headers=H)
check("Import на export с нови типове → 200", r.status_code == 200, f"got {r.status_code}")
reimported = client.get('/api/entries', headers=H).json()
check("Round-trip: 4 записа обратно", len(reimported) == 4, f"got {len(reimported)}")
reimported_fast = next(e for e in reimported if e['type'] == 'fast')
check("Round-trip: meal_time/carbs_g оцеляват", reimported_fast.get('carbs_g') == 45.0)
reimported_activity = next(e for e in reimported if e['type'] == 'activity')
check("Round-trip: meta оцелява", reimported_activity.get('meta') == {'intensity':'moderate'})


# ========== PROFILES CRUD ==========
section("Profiles API")

# Clean any existing profiles
existing = client.get('/api/profiles', headers=H).json()
for p in existing:
    client.delete(f'/api/profiles/{p["id"]}', headers=H)

# Create
r = client.post('/api/profiles', json={
    'name': 'Test Standard', 'active_from': '2026-11-01',
    'basal': [0.5]*24, 'isf': [{'from':'00:00','value':3.0}],
    'cr': [{'from':'00:00','value':12}], 'notes': 'QA test'
}, headers=H)
check("profiles POST → 200", r.status_code == 200, f"got {r.status_code}")
pid1 = r.json()['id']

r = client.post('/api/profiles', json={
    'name': 'Test v2', 'active_from': '2026-11-10',
    'basal': [0.6]*24, 'isf': [{'from':'00:00','value':4.0},{'from':'08:00','value':3.0}],
    'cr': [{'from':'00:00','value':12}], 'notes': ''
}, headers=H)
check("profiles POST #2 → 200", r.status_code == 200)
pid2 = r.json()['id']

# Validation: basal must have 24 values
r = client.post('/api/profiles', json={
    'name': 'Bad', 'active_from': '2026-11-01',
    'basal': [0.5]*10, 'isf': [], 'cr': []
}, headers=H)
check("profiles: basal != 24 → 400", r.status_code == 400, f"got {r.status_code}")

# Validation: name required
r = client.post('/api/profiles', json={
    'name': '', 'active_from': '2026-11-01',
    'basal': [0.5]*24, 'isf': [], 'cr': []
}, headers=H)
check("profiles: empty name → 422", r.status_code == 422, f"got {r.status_code}")

# List
r = client.get('/api/profiles', headers=H)
check("profiles GET list → 200", r.status_code == 200)
profiles = r.json()
check("profiles: 2 записа", len(profiles) == 2, f"got {len(profiles)}")
check("profiles: сортирани по active_from desc", profiles[0]['active_from'] >= profiles[1]['active_from'])

# Active
r = client.get('/api/profiles/active', headers=H)
check("profiles/active → 200", r.status_code == 200)
active = r.json()['active']
check("profiles/active: последният по дата", active['name'] == 'Test v2')
check("profiles/active: basal е list с 24", len(active['basal']) == 24)
check("profiles/active: isf е list", isinstance(active['isf'], list) and len(active['isf']) == 2)

# Update
r = client.put(f'/api/profiles/{pid1}', json={
    'name': 'Test Standard (updated)', 'active_from': '2026-11-01',
    'basal': [0.55]*24, 'isf': [{'from':'00:00','value':3.1}],
    'cr': [{'from':'00:00','value':10}], 'notes': 'updated'
}, headers=H)
check("profiles PUT → 200", r.status_code == 200)
check("profiles PUT: name updated", r.json()['name'] == 'Test Standard (updated)')

# Verify update persisted
r = client.get('/api/profiles', headers=H)
updated = next(p for p in r.json() if p['id'] == pid1)
check("profiles: update persisted", updated['notes'] == 'updated')
check("profiles: basal updated", updated['basal'][0] == 0.55)

# Update non-existent
r = client.put('/api/profiles/99999', json={
    'name': 'Ghost', 'active_from': '2026-01-01',
    'basal': [0.5]*24, 'isf': [], 'cr': []
}, headers=H)
check("profiles PUT 404", r.status_code == 404, f"got {r.status_code}")

# Delete
r = client.delete(f'/api/profiles/{pid1}', headers=H)
check("profiles DELETE → 200", r.status_code == 200)
r = client.delete(f'/api/profiles/{pid1}', headers=H)
check("profiles DELETE 404 (вече изтрит)", r.status_code == 404, f"got {r.status_code}")

# Cleanup
client.delete(f'/api/profiles/{pid2}', headers=H)
r = client.get('/api/profiles', headers=H)
check("profiles: cleanup OK (0 записа)", len(r.json()) == 0, f"got {len(r.json())}")


# ========== 13. P0: ЧЕТИМИ 422 + ДИАПАЗОНИ + АТОМАРЕН BATCH ==========
section("13. P0 — четими 422, диапазони по тип, атомарен batch")
client.delete('/api/entries', headers=H)
T0 = '2027-01-01T08:00:00+00:00'

def n_entries():
    return len(client.get('/api/entries', headers=H).json())

# --- 422 пази detail (list) и добавя message ---
r = client.post('/api/entries', json={'type':'bg','value':120,'time':T0}, headers=H)
j = r.json()
check("422 bg=120: detail е list", r.status_code == 422 and isinstance(j.get('detail'), list), str(j)[:200])
check("422 bg=120: message е четим (без JSON)", isinstance(j.get('message'), str) and 'mmol/L' in j['message'] and '{' not in j['message'], j.get('message'))
check("422 bg=120: подсказка за mg/dL", 'mg/dL' in j.get('message', ''), j.get('message'))
r = client.post('/api/entries', json={'type':'bg','value':None,'time':T0}, headers=H)
check("422 value=null: message назовава полето", r.status_code == 422 and 'value' in r.json().get('message',''), r.text[:200])

# --- диапазони по тип: (тип, валидна, невалидна) ---
cases = [('bg',1,0.9),('bg',30,30.1),('fast',0.05,0.01),('fast',50,50.5),('slow',50,51),
         ('ketones',0,10.1),('meal',1e-9,0),('meal',99,100),('activity',600,601),('activity',1,0.5),
         ('alcohol',0.5,0.4),('alcohol',10,11),('device_change',0,100)]
for i,(typ, ok_v, bad_v) in enumerate(cases):
    t = f'2027-01-02T{i:02d}:00:00+00:00'
    r = client.post('/api/entries', json={'type':typ,'value':ok_v,'time':t}, headers=H)
    check(f"{typ}={ok_v:g} → 200", r.status_code == 200, f"got {r.status_code} {r.text[:150]}")
    r = client.post('/api/entries', json={'type':typ,'value':bad_v,'time':t.replace('2027-01-02','2027-01-03')}, headers=H)
    check(f"{typ}={bad_v:g} → 422", r.status_code == 422, f"got {r.status_code}")
r = client.post('/api/entries', json={'type':'fast','value':2,'carbs_g':0,'time':T0}, headers=H)
check("fast carbs_g=0 → 422", r.status_code == 422)

# --- batch: всички валидни → 200, всички записани ---
client.delete('/api/entries', headers=H)
batch = {'entries': [
    {'type':'bg','value':6.7,'time':T0,'notes':'преди хранене'},
    {'type':'meal','value':3,'time':T0},
    {'type':'fast','value':4.5,'time':T0},
]}
r = client.post('/api/entries/batch', json=batch, headers=H)
check("batch 3 валидни → 200", r.status_code == 200, r.text[:200])
check("batch: count=3", r.json().get('count') == 3)
check("batch: 3 реда в базата", n_entries() == 3, str(n_entries()))

# --- повторение на същия batch (retry) → 200, без дубли ---
r = client.post('/api/entries/batch', json=batch, headers=H)
check("batch retry → 200", r.status_code == 200, r.text[:200])
check("batch retry: всички duplicate=true", all(e.get('duplicate') for e in r.json().get('entries', [])))
check("batch retry: пак 3 реда", n_entries() == 3, str(n_entries()))

# --- невалиден 2-ри елемент → 422, НИЩО не е записано ---
client.delete('/api/entries', headers=H)
T1 = '2027-01-04T08:00:00+00:00'
r = client.post('/api/entries/batch', json={'entries': [
    {'type':'bg','value':6.7,'time':T1}, {'type':'meal','value':0,'time':T1}, {'type':'fast','value':3,'time':T1}]}, headers=H)
check("batch с невалиден meal → 422", r.status_code == 422, f"got {r.status_code}")
check("batch 422: message сочи елемента (entries.1)", 'entries.1' in r.json().get('message',''), r.json().get('message'))
check("batch 422: 0 реда (няма частичен запис)", n_entries() == 0, str(n_entries()))

# --- лошо време на 3-ти елемент → 400, НИЩО не е записано ---
r = client.post('/api/entries/batch', json={'entries': [
    {'type':'bg','value':6.7,'time':T1}, {'type':'meal','value':2,'time':T1}, {'type':'fast','value':3,'time':'not-a-date'}]}, headers=H)
check("batch с лошо време → 400", r.status_code == 400, f"got {r.status_code}")
check("batch 400: detail сочи entries.2", 'entries.2' in str(r.json().get('detail','')), r.text[:200])
check("batch 400: 0 реда (няма частичен запис)", n_entries() == 0, str(n_entries()))

# --- DB грешка по средата → rollback на вече вмъкнатите ---
import sqlite3 as _sq
_orig = app_mod._insert_entry
_calls = {'n': 0}
def _boom(c, e, t, mt):
    _calls['n'] += 1
    if _calls['n'] == 2:
        raise _sq.OperationalError("simulated failure mid-batch")
    return _orig(c, e, t, mt)
app_mod._insert_entry = _boom
try:
    c_raise = TestClient(app_mod.app, raise_server_exceptions=False)
    r = c_raise.post('/api/entries/batch', json={'entries': [
        {'type':'bg','value':6.7,'time':T1}, {'type':'meal','value':2,'time':T1}]}, headers=H)
finally:
    app_mod._insert_entry = _orig
check("batch: грешка по средата → 500", r.status_code == 500, f"got {r.status_code}")
check("batch: rollback — 0 реда", n_entries() == 0, str(n_entries()))

# --- празен / твърде голям batch ---
r = client.post('/api/entries/batch', json={'entries': []}, headers=H)
check("batch празен → 422", r.status_code == 422)
r = client.post('/api/entries/batch', json={'entries': [{'type':'bg','value':5,'time':f'2027-02-01T{i:02d}:00:00'} for i in range(11)]}, headers=H)
check("batch 11 елемента → 422 (max 10)", r.status_code == 422)
r = TestClient(app_mod.app).post('/api/entries/batch', json=batch)
check("batch без auth → 401", r.status_code == 401, f"got {r.status_code}")

# --- калкулатор: BG > 30 (вероятно mg/dL) не дава доза ---
r = client.post('/api/calc-dose', json={'bg': 95, 'xe': 3}, headers=H)
check("calc-dose bg=95 → 422 (не смята доза от mg/dL)", r.status_code == 422, f"got {r.status_code}")
r = client.post('/api/calc-dose', json={'bg': 7, 'xe': 3}, headers=H)
check("calc-dose bg=7 → 200", r.status_code == 200, f"got {r.status_code}")
r = client.post('/api/calc-evening?bg=95', headers=H)
check("calc-evening bg=95 → 422", r.status_code == 422, f"got {r.status_code}")
client.delete('/api/entries', headers=H)


# ========== FINAL REPORT ==========
print("\n" + "="*60)
print(f"РЕЗУЛТАТ: {PASSED} passed, {FAILED} failed")
print("="*60)
if FAILURES:
    print("\nПРОВАЛИ:")
    for f in FAILURES:
        print(f"  ✗ {f}")

# Cleanup
shutil.rmtree(TEST_DIR, ignore_errors=True)
sys.exit(0 if FAILED == 0 else 1)
