"""Full E2E browser matrix (Phase G) — TEST INSTANCE ONLY (port 8766), includes destructive steps.
Covers: C every entry button, B notes edit (PATCH) + history delete, E settings, F profiles UI,
G cgm_view filters, H chart ranges + stats, J export→clear→import round-trip, K clear-all guard,
I report notes escaping (XSS).
usage: browser_full.py http://HOST:8766 ENV_FILE"""
import json, sys, time, urllib.request
from playwright.sync_api import sync_playwright

BASE, ENVF = sys.argv[1], sys.argv[2]
if not BASE.rstrip("/").endswith(":8766"):
    sys.exit("REFUSED: destructive E2E runs only against the test instance on :8766")
env = dict(l.strip().split("=", 1) for l in open(ENVF) if "=" in l)
DAY = "2020-01-15"
WIN = "/api/entries?from=2020-01-13&to=2020-01-17"
P = F = 0
slot = iter(range(1, 600))

def check(name, cond, info=""):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  →  {str(info)[:250]}")

def api(path, tok, method="GET", body=None, timeout=120):
    r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        t = resp.read().decode(); return json.loads(t) if t else None

with sync_playwright() as pw:
    br = pw.chromium.launch()
    ctx = br.new_context(); ctx.add_init_script("try{localStorage.setItem('diabet_lang','bg')}catch(e){}")
    page = ctx.new_page(); errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/", wait_until="networkidle")
    page.fill("#login-user", env["DIABET_USERNAME"]); page.fill("#login-pass", env["DIABET_PASSWORD"])
    page.click("button[onclick='doLogin()']"); page.wait_for_timeout(2000)
    tok = page.evaluate("localStorage.getItem('diabet_token')")
    ident = lambda r: (r["type"], r["value"], r["time"], r["notes"])
    start_rows = {ident(r) for r in api(WIN, tok)}

    def reveal(sel):
        page.evaluate("""sel => { let el = document.querySelector(sel);
            for (; el; el = el.parentElement) { if (el.hidden) el.hidden = false;
              if (getComputedStyle(el).display === 'none') el.style.display = 'block'; } }""", sel)

    def set_picker(prefix):
        n = next(slot); h, m = 8 + n // 60, n % 60
        page.evaluate(f"""(() => {{ if (typeof markPicker === 'function') markPicker('{prefix}', true);
            const d = document.getElementById('{prefix}-date'); if (!d) return;
            d.value='{DAY}'; document.getElementById('{prefix}-hour').value='{h}'; document.getElementById('{prefix}-min').value='{m}'; }})()""")

    def click_save(btn_sel):
        before = {r["id"] for r in api(WIN, tok)}
        page.evaluate("document.getElementById('toast').classList.remove('show')")
        reveal(btn_sel); page.click(btn_sel)
        page.wait_for_function("document.getElementById('toast').classList.contains('show')", timeout=10000)
        tt = page.inner_text("#toast"); err = page.evaluate("document.getElementById('toast').classList.contains('error')")
        new = [r for r in api(WIN, tok) if r["id"] not in before]
        return tt, err, new

    print("== C. всеки бутон за запис (index.html)")
    plan = [
        ("bg",            lambda: (page.evaluate("selectEntryType('bg')"), set_picker("bg"), page.fill("#bg-value", "6.4")), "button[onclick=\"addEntry('bg')\"]", "bg", None),
        ("бърз инсулин",  lambda: (page.evaluate("selectEntryType('fast')"), set_picker("fast"), page.fill("#fast-value", "3.5"), page.fill("#fast-carbs", "30")), "button[onclick=\"addEntry('fast')\"]", "fast", lambda r: r["carbs_g"] == 30),
        ("бавен инсулин", lambda: (page.evaluate("selectEntryType('slow')"), set_picker("slow"), page.fill("#slow-value", "14")), "button[onclick=\"addEntry('slow')\"]", "slow", None),
        ("кетони",        lambda: (page.evaluate("selectEntryType('ketones')"), set_picker("ketones"), page.fill("#ketones-value", "0.2")), "button[onclick=\"addEntry('ketones')\"]", "ketones", None),
        ("активност",     lambda: (page.evaluate("selectEntryType('activity')"), set_picker("activity"), page.fill("#activity-value", "40")), "button[onclick=\"addEntry('activity')\"]", "activity", lambda r: (r["meta"] or {}).get("intensity")),
        ("алкохол",       lambda: (page.evaluate("selectEntryType('alcohol')"), set_picker("alcohol"), page.fill("#alcohol-value", "1")), "button[onclick=\"addEntry('alcohol')\"]", "alcohol", None),
        ("устройство",    lambda: (page.evaluate("selectEntryType('device_change')"), set_picker("device_change")), "button[onclick=\"addEntry('device_change')\"]", "device_change", lambda r: (r["meta"] or {}).get("device_type")),
        ("хипо",          lambda: (reveal("#hypo-g"), set_picker("hypo"), page.fill("#hypo-g", "4"), page.fill("#hypo-n", "3")), "button[onclick='addHypo()']", "meal", lambda r: r["carbs_g"] == 12 and r["notes"].startswith("[ХИПО]")),
        ("цигара",        lambda: (reveal("#cig-n"), set_picker("cig"), page.fill("#cig-n", "2")), "button[onclick='addCig()']", "activity", lambda r: r["notes"].startswith("[ЦИГАРА]")),
        ("нощен комплект",lambda: (reveal("#nk-fast-g"), set_picker("nk"), page.fill("#nk-fast-g", "10"), page.fill("#nk-slow-g", "15")), "button[onclick='addNightKit()']", "meal", lambda r: r["carbs_g"] == 25),
        ("темп базал",    lambda: (reveal("#tb-pct"), set_picker("tb"), page.fill("#tb-pct", "70"), page.fill("#tb-dur", "60")), "button[onclick='addTempBasal()']", "device_change", lambda r: (r["meta"] or {}).get("temp_basal_pct") == 70),
        ("бърз болус",    lambda: (reveal("#qb-units"), set_picker("qb"), page.fill("#qb-units", "1.5")), "button[onclick='addQuickBolus()']", "fast", None),
        ("спиране",       lambda: (reveal("#susp-dur"), set_picker("susp"), page.fill("#susp-dur", "30")), "button[onclick='addSuspend()']", "device_change", lambda r: (r["meta"] or {}).get("temp_basal_pct") == 0),
        ("възобнови",     lambda: (reveal("button[onclick='addResume()']"), set_picker("resume")), "button[onclick='addResume()']", "device_change", lambda r: (r["meta"] or {}).get("resume") is True),
        ("период",        lambda: (reveal("#period-type"), page.fill("#period-start-date", DAY)), "button[onclick='addPeriod()']", "device_change", lambda r: (r["meta"] or {}).get("device_type") == "period"),
    ]
    for name, prep, btn, typ, extra in plan:
        try:
            prep()
            tt, err, new = click_save(btn)
            ok = (not err) and len(new) == 1 and new[0]["type"] == typ and (extra is None or extra(new[0]))
            check(f"{name} → 1 ред {typ} («{tt[:40]}»)", ok, (tt, [(r['type'], r['value'], r.get('meta')) for r in new]))
        except Exception as e:
            check(f"{name}", False, repr(e)[:200])

    print("== B/K. история: редакция на бележка (PATCH) + изтриване")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime())
    mk = api("/api/entries", tok, "POST", {"type": "bg", "value": 7.7, "time": now_iso, "notes": "e2e-note"})
    page.evaluate("currentRange = 1; reload()"); page.wait_for_timeout(2500)
    cell = page.query_selector(f".notes-cell[data-id='{mk['id']}']")
    if cell:
        cell.click(); page.wait_for_timeout(300)
        page.fill(".notes-edit-input", "e2e-edited"); page.keyboard.press("Enter"); page.wait_for_timeout(1500)
        row = next((r for r in api("/api/entries?from=" + now_iso[:10], tok) if r["id"] == mk["id"]), None)
        check("PATCH бележка → нов текст", row and row["notes"] == "e2e-edited", row)
        check("PATCH бележка → value/time непроменени", row and row["value"] == 7.7 and row["time"] == mk["time"], row)
    else:
        check("ред в историята за редакция", False, "notes cell not found")
    page.once("dialog", lambda d: d.accept())
    delbtn = page.query_selector(f"[onclick*='deleteEntry({mk['id']})']") or page.query_selector(f"button[data-id='{mk['id']}']")
    if delbtn:
        reveal(f"[onclick*='deleteEntry({mk['id']})']"); delbtn.click(); page.wait_for_timeout(1500)
        ids_now = {r["id"] for r in api("/api/entries?from=" + now_iso[:10], tok)}
        check("изтриване от историята → точно този ред", mk["id"] not in ids_now, mk["id"])
    else:
        api(f"/api/entries/{mk['id']}", tok, "DELETE")
        check("бутон за изтриване в историята", False, "delete button not found")

    print("== K. clear-all guard")
    n_before = len(api("/api/entries", tok))
    page.once("dialog", lambda d: d.dismiss())
    reveal("button[onclick='clearAll()']"); page.click("button[onclick='clearAll()']"); page.wait_for_timeout(1000)
    check("„Изтрий всичко“ + Отказ → нищо не е изтрито", len(api("/api/entries", tok)) == n_before)

    print("== H. графика: периоди + статистика")
    for rng in ["1", "3", "7", "30", "all"]:
        reveal(f"[data-range='{rng}']"); page.click(f"[data-range='{rng}']"); page.wait_for_timeout(2500 if rng == "all" else 1200)
        n = page.evaluate("entries.length")
        check(f"период {rng} → {n} записа, без грешка", n > 0 and not errors, errors[:2])
    page.click("[data-range='1']"); page.wait_for_timeout(1000)

    print("== E. настройки (XE грамове) запис + връщане")
    s0 = api("/api/settings", tok)
    reveal("#xe-grams-input"); page.fill("#xe-grams-input", "10")
    btn = page.query_selector("#xe-grams-input ~ button, button[onclick*='saveXe']")
    if btn:
        btn.click(); page.wait_for_timeout(1200)
        check("XE грамове → 10 запазено", api("/api/settings", tok).get("xe_grams_carbs") == 10)
    else:
        page.evaluate("document.getElementById('xe-grams-input').dispatchEvent(new Event('change'))"); page.wait_for_timeout(1200)
        check("XE грамове → 10 запазено (change)", api("/api/settings", tok).get("xe_grams_carbs") == 10, api("/api/settings", tok).get("xe_grams_carbs"))
    api("/api/settings", tok, "PUT", {"xe_grams_carbs": s0.get("xe_grams_carbs", 12)})
    check("XE грамове → върнато", api("/api/settings", tok).get("xe_grams_carbs") == s0.get("xe_grams_carbs", 12))

    print("== F. профили (profiles.html)")
    page.goto(BASE + "/static/profiles.html", wait_until="networkidle"); page.wait_for_timeout(1200)
    page.click("button[onclick='showForm()']"); page.wait_for_timeout(300)
    page.fill("#prof-name", "E2E profile"); page.fill("#prof-date", "2019-12-01")
    page.evaluate("document.querySelectorAll('#basal-grid input').forEach(i => i.value = '0.55')")
    page.click("button[onclick='saveProfile()']"); page.wait_for_timeout(1500)
    profs = api("/api/profiles", tok)
    mine = [p for p in profs if p["name"] == "E2E profile"]
    check("нов профил → записан", len(mine) == 1, [p["name"] for p in profs])
    if mine:
        page.once("dialog", lambda d: d.accept())
        page.click(f"[onclick*='deleteProfile({mine[0]['id']}']"); page.wait_for_timeout(1200)
        check("изтриване на профил", not [p for p in api("/api/profiles", tok) if p["name"] == "E2E profile"])
    if page.query_selector("button[onclick='toggleCompare()']"):
        page.click("button[onclick='toggleCompare()']"); page.wait_for_timeout(600)
        check("„Сравни“ се отваря без грешка", not errors, errors[:2])

    print("== G. cgm_view филтри")
    page.goto(BASE + "/static/cgm_view.html", wait_until="networkidle"); page.wait_for_timeout(1500)
    for v in ["12", "24", "72", "168", "whole"]:
        page.select_option("#rangeSelect", v); page.wait_for_timeout(2500 if v == "whole" else 1000)
        st = page.inner_text("#status")
        check(f"cgm_view период {v} → «{st[:50]}»", st and not errors, errors[:2])

    print("== I. отчет: бележки се ескейпват (XSS)")
    xss = api("/api/entries", tok, "POST", {"type": "bg", "value": 3.1, "time": f"{DAY}T05:00:00Z",
                                           "notes": "<img src=x onerror=\"window.__xss=1\">e2e"})
    page.goto(BASE + f"/static/report.html", wait_until="networkidle")
    page.evaluate(f"document.getElementById('from-date').value='{DAY}'; document.getElementById('to-date').value='{DAY}'")
    page.click("button[onclick*='load'], button[onclick*='generate'], button[onclick*='refresh']") if page.query_selector("button[onclick*='load'], button[onclick*='generate'], button[onclick*='refresh']") else None
    page.wait_for_timeout(2500)
    check("бележка с HTML не се изпълнява в отчета", page.evaluate("window.__xss") is None)
    check("бележката се показва като текст", "<img" in page.inner_text("body"), page.inner_text("body")[:200])
    api(f"/api/entries/{xss['id']}", tok, "DELETE")

    print("== J. export → изтрий всичко → import (round-trip, само тест)")
    exp = api("/api/export", tok, timeout=300)
    n_exp = sum(len(v) for v in exp.values())
    api("/api/entries", tok, "DELETE")
    check("след „изтрий всичко“ → 0 записа", len(api("/api/entries", tok)) == 0)
    payload = {**exp, "replace": False}
    res = api("/api/import", tok, "POST", payload, timeout=600)
    n_after = len(api("/api/entries", tok))
    check(f"import → {res.get('imported')} внесени, {n_after} в базата (export {n_exp})", n_after == n_exp, res)
    res2 = api("/api/import", tok, "POST", payload, timeout=600)
    check("повторен import → 0 нови (идемпотентен)", res2.get("imported") == 0, res2)

    print("== cleanup")
    for r in api(WIN, tok):      # ids change on import → compare by content
        if ident(r) not in start_rows: api(f"/api/entries/{r['id']}", tok, "DELETE")
    check("cleanup на тестовия ден", {ident(r) for r in api(WIN, tok)} == start_rows)
    check("няма JS грешки в целия поток", not errors, errors[:5])
    br.close()

print(f"\nBROWSER FULL E2E: {P} passed, {F} failed")
sys.exit(1 if F else 0)
