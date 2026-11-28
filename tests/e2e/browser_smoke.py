"""Browser smoke test (headless Chromium) for P0 UI paths.
usage: smoke.py BASE_URL ENV_FILE   — ENV_FILE has DIABET_USERNAME/PASSWORD; never printed."""
import json, re, sys, time, urllib.request
from playwright.sync_api import sync_playwright

BASE, ENVF = sys.argv[1], sys.argv[2]
import random
# Unique minute slots per run: entries are unique on (type, time) at minute precision.
SLOT = iter(random.sample(range(24 * 60), 200))
SMOKE_DATE = sys.argv[3] if len(sys.argv) > 3 else "2026-11-27"
from datetime import date, timedelta as _td
_d = date.fromisoformat(SMOKE_DATE)
WIN = f"/api/entries?from={_d - _td(days=2)}&to={_d + _td(days=2)}&limit=100000"
def slot():
    m = next(SLOT); return m // 60, m % 60
env = dict(l.strip().split("=", 1) for l in open(ENVF) if "=" in l)
P = F = 0
CYR = re.compile(r"[А-Яа-я]")

def check(name, cond, info=""):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  →  {str(info)[:250]}")

def api(path, tok, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                               headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=20) as resp:
        t = resp.read().decode(); return json.loads(t) if t else None

def ids(tok):
    return {r["id"] for r in api(WIN, tok)}

def new_rows(tok, before):
    return [r for r in api(WIN, tok) if r["id"] not in before]

def toast_text(page, timeout=6000):
    page.wait_for_function("document.getElementById('toast').classList.contains('show')", timeout=timeout)
    return page.inner_text("#toast")

with sync_playwright() as pw:
    br = pw.chromium.launch()
    ctx = br.new_context(locale="bg-BG")
    ctx.add_init_script("try{ if(!localStorage.getItem('diabet_lang')) localStorage.setItem('diabet_lang','bg') }catch(e){}")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    dialogs = []

    print("== index.html: login")
    page.goto(BASE + "/", wait_until="networkidle")
    page.fill("#login-user", env["DIABET_USERNAME"]); page.fill("#login-pass", env["DIABET_PASSWORD"])
    page.click("button[onclick='doLogin()']")
    page.wait_for_timeout(1500)
    tok = page.evaluate("localStorage.getItem('diabet_token')")
    check("login → token в localStorage (не null)", tok and tok != "null" and len(tok) > 20, tok)
    page.wait_for_function("typeof _uiReady === 'undefined' || _uiReady === true", timeout=10000)
    START_IDS = {r["id"] for r in api(WIN, tok)}

    def save(type_, value, dialog_action=None):
        dialogs.clear()
        def on_dialog(d):
            dialogs.append(d.message)
            d.accept() if dialog_action == "accept" else d.dismiss()
        page.once("dialog", on_dialog)
        page.evaluate(f"selectEntryType('{type_}')")
        h, m = slot()
        page.evaluate(f"""(() => {{ markPicker('{type_}', true);
            document.getElementById('{type_}-date').value='{SMOKE_DATE}';
            document.getElementById('{type_}-hour').value='{h}';
            document.getElementById('{type_}-min').value='{m}'; }})()""")
        page.fill(f"#{type_}-value", str(value))
        page.evaluate("document.getElementById('toast').classList.remove('show')")
        page.click(f"button[onclick=\"addEntry('{type_}')\"]")

    print("== index.html: mg/dL confirm")
    b = ids(tok)
    save("bg", 120, "dismiss")
    page.wait_for_timeout(800)
    check("КЗ 120 → confirm диалог с mg/dL и 6.7", dialogs and "mg/dL" in dialogs[0] and "6.7" in dialogs[0], dialogs)
    check("Отказ → нищо не е записано", not new_rows(tok, b), new_rows(tok, b))
    save("bg", 120, "accept")
    tt = toast_text(page)
    nr = new_rows(tok, b)
    check("Приемане → записан точно 1 ред: bg 6.7 mmol/L", [(r["type"], r["value"]) for r in nr] == [("bg", 6.7)], nr)
    check("Toast след запис е 'записано', без JSON", "{" not in tt, tt)

    print("== index.html: toast-ове при грешен вход")
    save("bg", 0)
    tt = toast_text(page)
    check(f"КЗ 0 → преведен toast: «{tt}»", "по-голяма от 0" in tt, tt)
    save("ketones", 15)
    tt = toast_text(page)
    check(f"Кетони 15 → toast с диапазон: «{tt}»", "0" in tt and "10" in tt and "{" not in tt, tt)
    save("fast", 0.01)
    tt = toast_text(page)
    check(f"Болус 0.01 → сървърна 422 като четим toast: «{tt}»", "0.05" in tt and "{" not in tt and "detail" not in tt, tt)

    print("== meal.html: калкулатор + атомарен запис")
    page.goto(BASE + "/static/meal.html" if "meal" not in page.url else page.url, wait_until="networkidle")
    if page.url.rstrip("/").endswith(":8766") or "/static/meal.html" not in page.url:
        page.goto(BASE + "/static/meal.html", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.fill("#m-bg", "120"); page.fill("#m-xe", "3"); page.dispatch_event("#m-bg", "input")
    page.wait_for_timeout(800)
    hint = page.inner_text("#calc-result")
    check(f"КЗ 120 → подсказка mg/dL, без доза: «{hint}»", "mg/dL" in hint and "6.7" in hint, hint)
    check("КЗ 120 → бутонът за запис е изключен", page.is_disabled("#m-save-btn"))

    b = ids(tok)
    page.fill("#m-bg", "12.5"); page.fill("#m-xe", "0"); page.dispatch_event("#m-xe", "input")
    page.wait_for_timeout(1200)
    check("0 XE (корекция) → бутонът е активен", not page.is_disabled("#m-save-btn"), page.inner_text("#calc-result")[:200])
    h, m = slot()
    page.evaluate(f"(() => {{ document.getElementById('m-date').value='{SMOKE_DATE}'; document.getElementById('m-hour').value='{h}'; document.getElementById('m-min').value='{m}'; }})()")
    page.evaluate("document.getElementById('toast').classList.remove('show')")
    page.click("#m-save-btn")
    tt = toast_text(page)
    nr = new_rows(tok, b)
    types = sorted(r["type"] for r in nr)
    check(f"0 XE → toast «{tt}»", "{" not in tt, tt)
    check(f"0 XE → записани КЗ + болус, без meal: {types}", types == ["bg", "fast"], nr)
    b = ids(tok)

    page.fill("#m-bg", "7.2"); page.fill("#m-xe", "3"); page.dispatch_event("#m-xe", "input")
    page.wait_for_timeout(1200)
    # different minute so (type,time) doesn't collide with the previous save
    h, m = slot()
    page.evaluate(f"(() => {{ document.getElementById('m-date').value='{SMOKE_DATE}'; document.getElementById('m-hour').value='{h}'; document.getElementById('m-min').value='{m}'; }})()")
    page.evaluate("document.getElementById('toast').classList.remove('show')")
    page.click("#m-save-btn")
    tt = toast_text(page)
    nr = new_rows(tok, b)
    types = sorted(r["type"] for r in nr)
    check(f"3 XE → записани КЗ + meal (+ болус): {types}", "bg" in types and "meal" in types, types)

    print("== подстраници превеждат (en, de)")
    for lang in ("en", "de"):
        page.evaluate(f"localStorage.setItem('diabet_lang','{lang}')")
        for sp in ("meal.html", "report.html", "print-review.html", "profiles.html", "cgm_view.html"):
            page.goto(f"{BASE}/static/{sp}", wait_until="networkidle")
            page.wait_for_timeout(700)
            vis = page.evaluate("""() => [...document.querySelectorAll('[data-i18n]')]
                .filter(e => e.children.length === 0)   // leaf UI labels only — containers hold user notes
                .filter(e => e.offsetParent !== null || e.tagName === 'TITLE').map(e => e.textContent.trim()).filter(Boolean)""")
            cyr = [m for v in vis for m in re.findall(r".{0,25}[А-Яа-я][А-Яа-я ]*.{0,15}", v.replace("\n", " "))]
            check(f"{lang} {sp}: {len(vis)} data-i18n елемента, на кирилица {len(cyr)}", vis and len(cyr) == 0, cyr[:5])
    page.evaluate("localStorage.setItem('diabet_lang','bg')")

    check("няма JS грешки в конзолата (pageerror)", not errors, errors[:5])
    # cleanup: delete every row this run created in the smoke window
    leftover = [r for r in api(WIN, tok) if r["id"] not in START_IDS]
    for r in leftover: api(f"/api/entries/{r['id']}", tok, "DELETE")
    check(f"cleanup: изтрити {len(leftover)} тестови реда", not [r for r in api(WIN, tok) if r["id"] not in START_IDS])
    br.close()

print(f"\nBROWSER SMOKE: {P} passed, {F} failed")
sys.exit(1 if F else 0)
