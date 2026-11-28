"""Browser checks for group 2: B1 (401 never silent), C1 (CGM sync errors), C2, C3.
usage: browser_auth_ux.py BASE_URL ENV_FILE [APIKEY_ONLY_BASE APIKEY_ONLY_KEY]
ENV_FILE holds DIABET_USERNAME/PASSWORD (never printed). Writes only on the 2020-01-15 test day
and deletes what it creates."""
import json, sys, urllib.request
from playwright.sync_api import sync_playwright

BASE, ENVF = sys.argv[1], sys.argv[2]
KEYONLY_BASE = sys.argv[3] if len(sys.argv) > 3 else None
KEYONLY_KEY = sys.argv[4] if len(sys.argv) > 4 else None
env = dict(l.strip().split("=", 1) for l in open(ENVF) if "=" in l)
DAY = "2020-01-15"
WIN = "/api/entries?from=2020-01-13&to=2020-01-17"
P = F = 0

def check(name, cond, info=""):
    global P, F
    if cond: P += 1; print(f"  ✓ {name}")
    else: F += 1; print(f"  ✗ {name}  →  {str(info)[:250]}")

def api(base, path, hdr, method="GET"):
    r = urllib.request.Request(base + path, method=method, headers=hdr)
    with urllib.request.urlopen(r, timeout=20) as resp:
        t = resp.read().decode(); return json.loads(t) if t else None

def banner(page):
    el = page.query_selector("#auth-banner")
    return el.inner_text() if el else ""

def visible(page, sel):
    el = page.query_selector(sel)
    return bool(el) and el.is_visible()

def login(page, base):
    page.goto(base + "/", wait_until="networkidle")
    page.fill("#login-user", env["DIABET_USERNAME"]); page.fill("#login-pass", env["DIABET_PASSWORD"])
    page.click("button[onclick='doLogin()']"); page.wait_for_timeout(1500)
    return page.evaluate("localStorage.getItem('diabet_token')")

def try_save_bg(page, value=6.1, minute=1):
    page.evaluate("document.getElementById('toast').classList.remove('show')")
    page.evaluate("selectEntryType('bg')")
    page.evaluate(f"""(() => {{ markPicker('bg', true);
        document.getElementById('bg-date').value='{DAY}';
        document.getElementById('bg-hour').value='9'; document.getElementById('bg-min').value='{minute}'; }})()""")
    page.fill("#bg-value", str(value))
    page.click("button[onclick=\"addEntry('bg')\"]")
    page.wait_for_timeout(1500)
    return page.inner_text("#toast") if page.evaluate("document.getElementById('toast').classList.contains('show')") else ""

with sync_playwright() as pw:
    br = pw.chromium.launch()
    errors = []

    def new_page():
        ctx = br.new_context()
        ctx.add_init_script("try{ if(!localStorage.getItem('diabet_lang')) localStorage.setItem('diabet_lang','bg') }catch(e){}")
        pg = ctx.new_page(); pg.on("pageerror", lambda e: errors.append(str(e)))
        return pg

    print("== B1: login режим, изтекъл/невалиден token")
    page = new_page()
    tok = login(page, BASE)
    H = {"Authorization": "Bearer " + tok}
    start_ids = {r["id"] for r in api(BASE, WIN, H)}
    page.evaluate("localStorage.setItem('diabet_token', 'expired.invalid.token')")
    tt = try_save_bg(page, 6.1, 1)
    check("невалиден token → банер „НЕ е записано“", "НЕ е записано" in banner(page), banner(page))
    check("невалиден token → login формата е показана", visible(page, "#login"))
    check("невалиден token → няма „Записано“ toast", "Записано" not in tt, tt)
    check("невалиден token → token е изчистен", page.evaluate("localStorage.getItem('diabet_token')") is None)
    check("невалиден token → нищо не е записано", {r["id"] for r in api(BASE, WIN, H)} == start_ids)
    # back in → banner gone, save works
    page.fill("#login-user", env["DIABET_USERNAME"]); page.fill("#login-pass", env["DIABET_PASSWORD"])
    page.click("button[onclick='doLogin()']"); page.wait_for_timeout(1500)
    check("след нов login → банерът изчезва", not banner(page), banner(page))
    tt = try_save_bg(page, 6.2, 2)
    check(f"след нов login → записът минава («{tt}»)", "Записано" in tt, tt)

    print("== B1: stale API ключ + без token (cache-clear сценарий)")
    page.evaluate("localStorage.removeItem('diabet_token'); localStorage.setItem('diabet_api_key', 'stale-wrong-key')")
    page.reload(wait_until="networkidle"); page.wait_for_timeout(1200)
    check("стар ключ, без token → login формата е показана", visible(page, "#login"))
    page.fill("#login-user", env["DIABET_USERNAME"]); page.fill("#login-pass", env["DIABET_PASSWORD"])
    page.click("button[onclick='doLogin()']"); page.wait_for_timeout(1500)
    check("стар ключ → изчистен след login", page.evaluate("localStorage.getItem('diabet_api_key')") is None)

    print("== B1: logout чисти всички креди")
    check("(предусловие) логнат", bool(page.evaluate("localStorage.getItem('diabet_token')")))
    page.evaluate("localStorage.setItem('diabet_api_key', 'some-key')")
    page.evaluate("logout()")
    check("logout → token и ключ изчистени",
          page.evaluate("[localStorage.getItem('diabet_token'), localStorage.getItem('diabet_api_key')]") == [None, None])

    print("== B1: подстраници при 401")
    page = new_page()
    page.goto(BASE + "/static/profiles.html", wait_until="networkidle"); page.wait_for_timeout(1500)
    b = banner(page)
    check("profiles без креди → банер с бутон за вход", "НЕ е записано" in b and page.query_selector("#auth-banner button") is not None, b)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.goto(BASE + "/static/cgm_view.html", wait_until="networkidle"); page.wait_for_timeout(1500)
    check("cgm_view в login режим → банер, без prompt за API ключ", banner(page) and not dialogs, (banner(page), dialogs))

    print("== C1/C2/C3")
    page = new_page(); tok = login(page, BASE)
    page.evaluate("document.getElementById('toast').classList.remove('show')")
    page.click("#cgm-sync-btn"); page.wait_for_function("document.getElementById('toast').classList.contains('show')", timeout=150000)
    tt = page.inner_text("#toast")
    failed = page.evaluate("document.getElementById('toast').classList.contains('error')")
    if failed:   # test instance: no Juggluco config → must be a classified, readable error
        check(f"CGM sync грешка → ясно съобщение («{tt[:90]}»)", "not configured" in tt or "not reachable" in tt, tt)
        check("CGM sync грешка → няма „[info] requesting“", "[info]" not in tt, tt)
        page.wait_for_timeout(4000)
        check("грешката стои видима ≥ 4 s", page.evaluate("document.getElementById('toast').classList.contains('show')"))
    else:        # live: a real sync — the toast must report what happened
        check(f"CGM sync успех → обобщение («{tt[:90]}»)", "pushed" in tt or "CGM" in tt, tt)
    check("бутонът за sync е пак активен", not page.is_disabled("#cgm-sync-btn"))

    page.goto(BASE + "/static/print-review.html", wait_until="networkidle"); page.wait_for_timeout(2500)
    txt = page.inner_text("body")
    check("print-review: няма „undefined“", "undefined" not in txt, [l for l in txt.splitlines() if "undefined" in l][:3])

    page.goto(BASE + "/static/meal.html", wait_until="networkidle"); page.wait_for_timeout(800)
    page.evaluate(f"(() => {{ document.getElementById('m-date').value='{DAY}'; document.getElementById('m-hour').value='10'; document.getElementById('m-min').value='7'; }})()")
    page.fill("#m-bg", "12.5"); page.fill("#m-xe", "0"); page.dispatch_event("#m-xe", "input"); page.wait_for_timeout(1200)
    page.evaluate("document.getElementById('toast').classList.remove('show')")
    page.click("#m-save-btn"); page.wait_for_function("document.getElementById('toast').classList.contains('show')")
    tt = page.inner_text("#toast")
    check(f"0 XE toast без XE («{tt}»)", "XE" not in tt and "корекция" in tt, tt)

    # cleanup login-mode writes
    H = {"Authorization": "Bearer " + page.evaluate("localStorage.getItem('diabet_token')")}
    for r in api(BASE, WIN, H):
        if r["id"] not in start_ids: api(BASE, f"/api/entries/{r['id']}", H, "DELETE")
    check("cleanup login инстанция", {r["id"] for r in api(BASE, WIN, H)} == start_ids)

    if KEYONLY_BASE:
        print("== B1: само API ключ (без login)")
        page = new_page()
        page.goto(KEYONLY_BASE + "/", wait_until="networkidle"); page.wait_for_timeout(1500)
        check("без ключ → банер за невалиден ключ", "API ключът" in banner(page), banner(page))
        page.evaluate("localStorage.setItem('diabet_api_key', 'wrong')")
        page.reload(wait_until="networkidle"); page.wait_for_timeout(1200)
        tt = try_save_bg(page, 6.3, 3)
        check("грешен ключ → банер, без „Записано“", "API ключът" in banner(page) and "Записано" not in tt, (banner(page), tt))
        check("грешен ключ → изчистен", page.evaluate("localStorage.getItem('diabet_api_key')") is None)
        page.once("dialog", lambda d: d.accept(KEYONLY_KEY))
        page.click("#auth-banner button"); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
        check("„Въведи ключ“ → ключът е запазен, банерът изчезва",
              page.evaluate("localStorage.getItem('diabet_api_key')") == KEYONLY_KEY and not banner(page), banner(page))
        tt = try_save_bg(page, 6.4, 4)
        check(f"с верен ключ → записът минава («{tt}»)", "Записано" in tt, tt)

    check("няма JS грешки", not errors, errors[:5])
    br.close()

print(f"\nBROWSER AUTH/UX: {P} passed, {F} failed")
sys.exit(1 if F else 0)
