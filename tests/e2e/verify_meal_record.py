"""Verification of a meal record end-to-end (cleanup task #1). TEST INSTANCE ONLY.
Flow A: meal.html — BG + XE + food + OVERRIDE dose.   Flow B: index fast form — dose + carbs_g + separate meal_time.
Checks the DB rows, then where each field shows up: history (index), doctor report, CSV export.
Creates entries "now" on the test DB and deletes them at the end.
usage: verify_meal_record.py http://HOST:8766 ENV_FILE"""
import csv, io, json, sys, urllib.request
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

BASE, ENVF = sys.argv[1], sys.argv[2]
if not BASE.rstrip("/").endswith(":8766"):
    sys.exit("REFUSED: test instance only")
env = dict(l.strip().split("=", 1) for l in open(ENVF) if "=" in l)
FOOD = "e2e-food-oats"

def api(path, tok, method="GET"):
    r = urllib.request.Request(BASE + path, method=method, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(r, timeout=60) as resp:
        t = resp.read().decode(); return json.loads(t) if t else None

rows_out = []
def row(field, stored, history, report, csv_):
    rows_out.append((field, stored, history, report, csv_))

with sync_playwright() as pw:
    br = pw.chromium.launch(); ctx = br.new_context(accept_downloads=True)
    ctx.add_init_script("localStorage.setItem('diabet_lang','bg')")
    pg = ctx.new_page()
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.fill("#login-user", env["DIABET_USERNAME"]); pg.fill("#login-pass", env["DIABET_PASSWORD"])
    pg.click("button[onclick='doLogin()']"); pg.wait_for_timeout(1500)
    tok = pg.evaluate("localStorage.getItem('diabet_token')")
    since = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    before = {r["id"] for r in api(f"/api/entries?from={since}", tok)}

    # ---- Flow A: meal.html with override ----
    pg.goto(BASE + "/static/meal.html", wait_until="networkidle"); pg.wait_for_timeout(800)
    pg.evaluate("(() => { const d=new Date(Date.now()-40*60000); document.getElementById('m-hour').value=d.getHours(); document.getElementById('m-min').value=d.getMinutes(); })()")
    pg.fill("#m-bg", "7.2"); pg.fill("#m-xe", "3"); pg.fill("#m-food", FOOD); pg.dispatch_event("#m-xe", "input"); pg.wait_for_timeout(1500)
    pg.fill("#m-carbs", "36")
    pg.check("#m-mealtime-chk")
    pg.evaluate("(() => { const d=new Date(Date.now()-55*60000); document.getElementById('mm-hour').value=d.getHours(); document.getElementById('mm-min').value=d.getMinutes(); })()")
    recommended = pg.evaluate("calcData && calcData.recommended_units")
    pg.check("#m-override-chk"); pg.fill("#m-override", "4.5"); pg.dispatch_event("#m-override", "input")
    pg.click("#m-save-btn"); pg.wait_for_timeout(2000)
    a_rows = [r for r in api(f"/api/entries?from={since}", tok) if r["id"] not in before]
    a = {r["type"]: r for r in a_rows}
    before |= {r["id"] for r in a_rows}

    # ---- Flow B: index fast form with carbs + separate meal time ----
    pg.goto(BASE + "/", wait_until="networkidle"); pg.wait_for_timeout(1500)
    pg.evaluate("selectEntryType('fast')")
    pg.evaluate("(() => { markPicker('fast', true); const d=new Date(Date.now()-20*60000); document.getElementById('fast-hour').value=d.getHours(); document.getElementById('fast-min').value=d.getMinutes(); })()")
    pg.fill("#fast-value", "3.5"); pg.fill("#fast-carbs", "45"); pg.fill("#fast-notes", FOOD + "-B")
    pg.check("#fast-meal-time-toggle")
    # a user typing fires 'input' → picker marked touched; emulate that, else save resets it to "now"
    pg.evaluate("(() => { markPicker('fast-meal', true); const d=new Date(Date.now()-35*60000); document.getElementById('fast-meal-hour').value=d.getHours(); document.getElementById('fast-meal-min').value=d.getMinutes(); })()")
    pg.click("button[onclick=\"addEntry('fast')\"]"); pg.wait_for_timeout(2000)
    b = next((r for r in api(f"/api/entries?from={since}", tok) if r["id"] not in before), None)

    # ---- outputs ----
    pg.evaluate("currentRange = 1; reload()"); pg.wait_for_timeout(2500)
    hist = pg.inner_text("#history") if pg.query_selector("#history") else pg.inner_text("body")
    today = datetime.now().strftime("%Y-%m-%d")
    pg.goto(BASE + "/static/report.html", wait_until="networkidle")
    pg.evaluate(f"document.getElementById('from-date').value='{today}'; document.getElementById('to-date').value='{today}'")
    btn = pg.query_selector("button[onclick*='load'], button[onclick*='generate'], button[onclick*='refresh']")
    if btn: btn.click()
    pg.wait_for_timeout(3000); rep = pg.inner_text("body")
    pg.goto(BASE + "/", wait_until="networkidle"); pg.wait_for_timeout(1200)
    pg.evaluate(f"document.getElementById('exp-from').value='{today}'; document.getElementById('exp-to').value='{today}'")
    with pg.expect_download() as dl:
        pg.evaluate("exportCSV()")
    csv_rows = list(csv.reader(io.StringIO(open(dl.value.path(), encoding="utf8").read())))
    hdr = csv_rows[0]; cr = [dict(zip(hdr, r)) for r in csv_rows[1:]]

    def in_csv(pred): return any(pred(r) for r in cr)
    fa, ma, ba = a.get("fast"), a.get("meal"), a.get("bg")
    print(f"Flow A: calculator recommended {recommended} U, override typed 4.5 U")
    print("  rows saved:", {k: (v['value'], v['time'][11:16], v['notes'], v['carbs_g'], v['meal_time']) for k, v in a.items()})
    print("Flow B:", (b['value'], b['time'][11:16], b['notes'], b['carbs_g'], (b['meal_time'] or '')[11:16]) if b else None)
    yes = lambda c: "✓" if c else "✗"
    row("A доза = override (4.5), не препоръката", yes(fa and fa["value"] == 4.5), yes("4.5 U" in hist), yes("4.5" in rep), yes(in_csv(lambda r: r["type"] == "FAST" and r["value"] == "4.5")))
    row("A храна (notes)", yes(ma and ma["notes"] == FOOD), yes(FOOD in hist), yes(FOOD in rep), yes(in_csv(lambda r: r["notes"] == FOOD)))
    row("A XE (meal.value)", yes(ma and ma["value"] == 3), yes("3 XE" in hist), yes(FOOD in rep or "XE" in rep), yes(in_csv(lambda r: r["type"] == "MEAL" and r["value"] == "3")))
    rep_src = urllib.request.urlopen(BASE + "/static/report.html").read().decode()
    hm = lambda iso: datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    row("A carbs_g = 36 (ред хранене)", yes(ma and ma["carbs_g"] == 36), yes("36 g" in hist), yes("36 g" in rep), yes(in_csv(lambda r: r["type"] == "MEAL" and r["carbs_g"] == "36")))
    row("A meal_time ≠ time (хранене + доза)", yes(ma and fa and ma["meal_time"] and fa["meal_time"] == ma["meal_time"] and ma["meal_time"] < ma["time"]),
        yes(ma and ma["meal_time"] and hm(ma["meal_time"]) in hist), yes(ma and ma["meal_time"] and hm(ma["meal_time"]) in rep), yes(in_csv(lambda r: r["type"] == "MEAL" and r["meal_time"] and r["meal_time"] != r["time_utc"])))
    row("A КЗ ред без carbs/meal_time (не се дублира)", yes(ba and ba["carbs_g"] is None and ba["meal_time"] is None), "—", "—", "—")
    row("B доза (fast.value)", yes(b and b["value"] == 3.5), yes("3.5 U" in hist), yes("3.5" in rep), yes(in_csv(lambda r: r["type"] == "FAST" and r["value"] == "3.5")))
    row("B carbs_g = 45", yes(b and b["carbs_g"] == 45), yes("45 g" in hist), yes("45 g" in rep), yes(in_csv(lambda r: r["carbs_g"] == "45")))
    row("B meal_time ≠ time", yes(b and b["meal_time"] and b["meal_time"] < b["time"]), yes(b and b["meal_time"] and hm(b["meal_time"]) in hist), yes(b and b["meal_time"] and hm(b["meal_time"]) in rep), yes(in_csv(lambda r: r["meal_time"] and r["meal_time"] != r["time_utc"])))
    row("B бележка", yes(b and b["notes"] == FOOD + "-B"), yes(FOOD + "-B" in hist), yes(FOOD + "-B" in rep), yes(in_csv(lambda r: r["notes"] == FOOD + "-B")))

    for r in [*a.values(), b]:
        if r: api(f"/api/entries/{r['id']}", tok, "DELETE")
    left = [r for r in api(f"/api/entries?from={since}", tok) if FOOD in (r["notes"] or "") or (r["notes"] or "").startswith("за ")]
    print("cleanup leftovers:", len(left))
    br.close()

print("\n| поле | пази се | история | отчет | CSV |\n|---|---|---|---|---|")
for r in rows_out: print("| " + " | ".join(r) + " |")
