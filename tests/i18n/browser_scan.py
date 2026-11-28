"""Runtime i18n scan: 6 pages x 10 languages on a THROWAWAY instance seeded with Latin-only data,
so any visible Cyrillic is untranslated UI (minus ALLOW: persisted data markers / settings values).
Also flags raw i18n keys leaking to the UI (t() returns the key when it is missing).
usage: browser_scan.py BASE_URL API_KEY [REPO_STATIC_DIR_for_file_check]"""
import json, re, sys, urllib.request, pathlib
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

BASE, KEY = sys.argv[1], sys.argv[2]
STATIC = sys.argv[3] if len(sys.argv) > 3 else None
assert ":8766" not in BASE and ":8765" not in BASE, "seeds data — throwaway instance only"
LANGS = ["bg", "en", "de", "es", "fr", "it", "nl", "pl", "pt-PT", "pt-BR"]
CYR = re.compile(r"[А-Яа-яЁё]")
ALLOW = re.compile(r"\[(CGM|ХИПО|ПЕРИОД|ЦИГАРА|Глюкомер-BT)\]|до 200g кисело мляко|Български")
KEYLIKE = re.compile(r"\b[a-z]{2,}(?:_[a-z0-9]+){1,}\b")
KEY_ALLOW = {"bolus_kind", "insulin_type", "device_type", "carbs_g", "meal_time", "time_utc", "time_local",
             "temp_basal_pct", "temp_basal_min", "patch_site", "settings_change", "cgm_manual", "period_type"}

def post(path, body, method="POST"):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method=method,
                               headers={"X-API-Key": KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=20) as resp: return json.loads(resp.read() or b"null")

def seed():
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    batch = []
    for i in range(0, 3 * 24 * 60, 15):
        t = now - timedelta(minutes=i)
        v = 7 + 4 * ((i // 60) % 7 - 3) / 3
        if (i // 15) % 40 == 7: v = 3.2
        if (i // 15) % 40 == 20: v = 15.5
        batch.append({"type": "bg", "value": round(max(2.5, v), 1), "time": t.isoformat(), "notes": "[CGM]"})
    for k in range(3):
        d = now - timedelta(days=k)
        batch += [
            {"type": "bg", "value": 6.1, "time": (d - timedelta(minutes=7)).isoformat(), "notes": "fasting", "meta": {"source": "own"}},
            {"type": "meal", "value": 3, "time": (d - timedelta(hours=3)).isoformat(), "notes": "oats", "carbs_g": 36},
            {"type": "fast", "value": 4.5, "time": (d - timedelta(hours=3)).isoformat(), "notes": "lunch",
             "meta": {"delivery": "pump", "insulin_type": "fiasp", "bolus_kind": "normal"}},
            {"type": "slow", "value": 14, "time": (d - timedelta(hours=10)).isoformat(), "notes": "evening",
             "meta": {"delivery": "pen", "insulin_type": "tresiba"}},
            {"type": "ketones", "value": 0.2, "time": (d - timedelta(hours=5)).isoformat(), "notes": "check"},
            {"type": "activity", "value": 30, "time": (d - timedelta(hours=6)).isoformat(), "notes": "walk", "meta": {"intensity": "moderate"}},
            {"type": "alcohol", "value": 1, "time": (d - timedelta(hours=8)).isoformat(), "notes": "wine"},
            {"type": "device_change", "value": 0, "time": (d - timedelta(hours=9)).isoformat(), "notes": "new patch", "meta": {"device_type": "patch"}},
        ]
    for i in range(0, len(batch), 10):
        post("/api/entries/batch", {"entries": batch[i:i + 10]})
    try:
        post("/api/profiles", {"name": "Profile A", "active_from": (now - timedelta(days=5)).date().isoformat(),
                               "basal": [0.5] * 24, "isf": [], "cr": [], "notes": "test"})
    except Exception as e:
        print("  (profile seed skipped:", e, ")")
    return len(batch)

def visible_texts(page):
    return page.evaluate("""() => {
      const out = [];
      const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (w.nextNode()) {
        const n = w.currentNode, el = n.parentElement, s = n.textContent.trim();
        if (!s || !el) continue;
        if (['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName)) continue;
        const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || (r.width === 0 && r.height === 0)) continue;
        out.push(s);
      }
      document.querySelectorAll('input[placeholder],textarea[placeholder]').forEach(e => { if (e.offsetParent) out.push(e.placeholder); });
      document.querySelectorAll('[title]').forEach(e => { if (e.offsetParent) out.push(e.title); });
      out.push(document.title);
      return out;
    }""")

def problems(texts):
    cyr = [t for t in texts if CYR.search(t) and not ALLOW.search(t)]
    keys = [m for t in texts for m in KEYLIKE.findall(t) if m not in KEY_ALLOW and not t.startswith("[")]
    return cyr, keys

PAGES = {
    "index": ("/", lambda p: [p.evaluate(f"selectEntryType('{ty}')") or p.wait_for_timeout(150) for ty in
                              p.evaluate("[...document.querySelectorAll('.type-chip[data-type]')].map(c => c.dataset.type)")]),
    "meal": ("/static/meal.html", lambda p: [p.click(f".tab[data-tab='{tb}']") or p.wait_for_timeout(300) for tb in
                                             p.evaluate("[...document.querySelectorAll('.tab[data-tab]')].map(t => t.dataset.tab)")]),
    "report": ("/static/report.html", None),
    "print-review": ("/static/print-review.html", None),
    "profiles": ("/static/profiles.html", None),
    "cgm_view": ("/static/cgm_view.html", None),
}

print(f"seeded {seed()} entries")
fails = 0
with sync_playwright() as pw:
    br = pw.chromium.launch()
    # self-test: the scanner must catch an injected untranslated string and a raw key
    ctx = br.new_context(); ctx.add_init_script(f"localStorage.setItem('diabet_lang','en'); localStorage.setItem('diabet_api_key','{KEY}');")
    page = ctx.new_page(); page.goto(BASE + "/static/report.html", wait_until="networkidle"); page.wait_for_timeout(1500)
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div>Непреведен текст</div><div>report_missing_key</div>')")
    c, k = problems(visible_texts(page))
    assert any("Непреведен" in x for x in c) and "report_missing_key" in k, ("SELF-TEST FAILED", c, k)
    print(f"  self-test OK (scanner sees {len(visible_texts(page))} text nodes on en/report)")
    ctx.close()
    for lang in LANGS:
        ctx = br.new_context()
        ctx.add_init_script(f"localStorage.setItem('diabet_lang','{lang}'); localStorage.setItem('diabet_api_key','{KEY}');")
        page = ctx.new_page(); errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        row = []
        for name, (path, walk) in PAGES.items():
            page.goto(BASE + path, wait_until="networkidle"); page.wait_for_timeout(1500)
            texts = visible_texts(page)
            if walk:
                walk(page)   # visit every tab / entry type; collect after each
                texts += visible_texts(page)
            cyr, keys = ([], []) if lang == "bg" else problems(texts)
            if lang == "bg":
                _, keys = problems(texts)
            bad = len(set(cyr)) + len(set(keys))
            fails += bad
            row.append(f"{name}:{'OK' if not bad else bad}({len(set(texts))})")
            for c in sorted(set(cyr))[:8]: print(f"    {lang} {name} CYR: {c[:90]}")
            for k in sorted(set(keys))[:8]: print(f"    {lang} {name} KEY: {k}")
        if errs: fails += len(errs); print(f"    {lang} JS errors: {errs[:3]}")
        print(f"  {lang:6s} " + "  ".join(row))
        ctx.close()
    if STATIC:   # bg.js inline fallback: file:// demo mode, no fetch of JSON possible
        ctx = br.new_context(); page = ctx.new_page(); errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(pathlib.Path(STATIC, "index.html").resolve().as_uri(), wait_until="load"); page.wait_for_timeout(2500)
        ok = page.evaluate("typeof t === 'function' && t('nav_chart') !== 'nav_chart'")
        print(f"  file:// demo (bg.js fallback): {'OK' if ok and not errs else 'FAIL'} {errs[:2]}")
        fails += 0 if ok and not errs else 1
    br.close()
print(f"\nI18N RUNTIME: {'PASS' if not fails else f'{fails} problems'}")
sys.exit(1 if fails else 0)
