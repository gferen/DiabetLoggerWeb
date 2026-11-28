"""Example doses for owner review: runs the real calc_dose against a given profile at fixed
local times, flag OFF (today) vs flag ON (profile). Isolated temp DB, writes nothing real.
usage (inside the app image): PROFILE_JSON='{...}' python calc_examples.py"""
import json, os, sys, tempfile
from datetime import datetime as _dt, timezone
tmp = tempfile.mkdtemp(); os.environ["DIABET_DB"] = f"{tmp}/x.db"; os.environ["DIABET_SECRET_FILE"] = f"{tmp}/s"
os.environ.pop("DIABET_API_KEY", None); os.environ.pop("DIABET_USERNAME", None); os.environ.pop("DIABET_PASSWORD", None)
sys.path.insert(0, "/app")
import app
prof = json.loads(os.environ["PROFILE_JSON"])
with app.db() as c:
    c.execute("INSERT INTO basal_profiles (name, active_from, basal, isf, cr) VALUES (?,?,?,?,?)",
              (prof["name"], "2020-01-01", json.dumps([0.5] * 24), json.dumps(prof["isf"]), json.dumps(prof["cr"])))
tz = app.get_local_tz()
class FixedDT(_dt):
    at = None
    @classmethod
    def now(cls, tz_=None): return cls.at.astimezone(tz_) if tz_ else cls.at
def run(bg, xe, hhmm, on):
    FixedDT.at = _dt.fromisoformat(f"2026-11-29T{hhmm}:00").replace(tzinfo=tz).astimezone(timezone.utc)
    app.datetime = FixedDT; app.CALC_FROM_PROFILE = on
    return app.calc_dose(app.DoseCalcPayload(bg=bg, xe=xe))
s = app.get_settings()
print(f"Профил: {prof['name']} · CR {prof['cr']} · ISF {prof['isf']} · цел (горна) {s['target_bg_high']} · 1 XE = {s['xe_grams_carbs']} g\n")
print("| Час | КЗ | XE | Сега (DEFAULT_RULES) | От профила | Разбивка (профил) |\n|---|---|---|---|---|---|")
for hhmm in ("07:00", "09:00", "13:00", "22:00"):
    for bg, xe in ((6.0, 1), (6.0, 2), (6.0, 3), (10.0, 0), (13.0, 0), (16.0, 0), (10.0, 3)):
        old = run(bg, xe, hhmm, False); new = run(bg, xe, hhmm, True)
        src = new["source"]
        print(f"| {hhmm} | {bg} | {xe} | {old['recommended_units']} U | **{new['recommended_units']} U** | "
              f"{xe}×{new['ic_ratio']} + корекция {new['correction_units']} (ISF {src.get('isf')}) |")
print("\nАко горната цел е 8.5 (текущата ти цел 5.5–8.5):")
s2 = dict(s); s2["target_bg_high"] = 8.5
app.get_settings = lambda: s2
for hhmm in ("09:00", "13:00"):
    for bg in (10.0, 13.0, 16.0):
        new = run(bg, 0, hhmm, True)
        print(f"  {hhmm} КЗ {bg}: корекция {new['correction_units']} U (ISF {new['source']['isf']})")
