"""Static i18n scan: Cyrillic UI text that is not wired to i18n.
usage: python3 tests/i18n/scan_cyrillic.py [static_dir]   (exit 1 if findings)
HTML: Cyrillic text whose element (or an ancestor ≤2 up) has no data-i18n*; Cyrillic in
title/placeholder/aria-label without data-i18n-title/-placeholder/-aria.
JS: Cyrillic string literals outside comments, minus ALLOW (data persisted in notes/settings,
matched by reports — must stay Bulgarian) and t()-fallback defaults."""
import re, sys, pathlib
from html.parser import HTMLParser

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "static")
PAGES = ["index.html", "meal.html", "report.html", "print-review.html", "profiles.html", "cgm_view.html"]
CYR = re.compile(r"[А-Яа-яЁё]")
# Persisted data markers / values — stored in notes or settings and matched later. Never translate.
ALLOW = re.compile(r"\[(ХИПО|ПЕРИОД|ЦИГАРА|Глюкомер-BT|CGM)\]|^преди|^корекция$|^вечерна доза|преди вечерна доза|"
                   r"Болница|Пътуване|Болест|Стрес|Активен ден|Друго|бързи|бавни|бр\.|до 200g кисело мляко|"
                   r"override за|авто-калк|снак|след |^ до $| до |\(продължава\)|Нощен|Български")

class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack = []; self.out = []; self.in_script = False
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"): self.in_script = True
        for attr, need in (("title", "data-i18n-title"), ("placeholder", "data-i18n-placeholder"), ("aria-label", "data-i18n-aria")):
            if a.get(attr) and CYR.search(a[attr]) and need not in a:
                self.out.append((self.getpos()[0], f'{tag}[{attr}]', a[attr]))
        if tag not in ("br", "img", "input", "meta", "link", "hr", "source"):
            self.stack.append((tag, a))
    def handle_endtag(self, tag):
        if tag in ("script", "style"): self.in_script = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag: del self.stack[i:]; break
    def handle_data(self, data):
        if self.in_script or not CYR.search(data): return
        txt = data.strip()
        if ALLOW.search(txt): return
        wired = any(any(k.startswith("data-i18n") for k in a) for _, a in self.stack[-3:])
        if not wired:
            self.out.append((self.getpos()[0], "text", txt[:80]))

def js_findings(src, base_line):
    out = []
    # strip comments (keep line structure)
    code = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    code = re.sub(r"i18n-ignore-start.*?i18n-ignore-end", lambda m: "\n" * m.group(0).count("\n"), code, flags=re.S)
    for i, line in enumerate(code.split("\n")):
        line_nc = re.sub(r"(^|[^:])//.*$", r"\1", line)
        for m in re.finditer(r"(['\"`])((?:\\.|(?!\1).)*)\1", line_nc):
            lit = m.group(2)
            if not CYR.search(lit) or ALLOW.search(lit): continue
            pre = line_nc[:m.start()]
            if re.search(r"t\([^)]*\)\s*\|\|\s*$", pre): continue          # t('k') || 'fallback'
            if re.search(r"(console\.\w+|DEMO|demo)\(", line_nc): continue
            out.append((base_line + i, "js", lit[:80]))
    return out

total = 0
for page in PAGES:
    src = (ROOT / page).read_text(encoding="utf8")
    p = P(); p.feed(src); found = list(p.out)
    for m in re.finditer(r"<script(?![^>]*src)[^>]*>(.*?)</script>", src, re.S):
        found += js_findings(m.group(1), src[:m.start(1)].count("\n") + 1)
    total += len(found)
    print(f"== {page}: {len(found)}")
    for ln, kind, txt in found[:400]:
        print(f"   {page}:{ln} [{kind}] {txt}")
print(f"\nTOTAL: {total}")
sys.exit(1 if total else 0)
