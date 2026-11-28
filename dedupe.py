#!/usr/bin/env python3
"""
Намира (и по желание изтрива) дублирани записи в Diabet Logger базата.

Дубликат = два или повече записа с еднакви (type, time, value, meta).
Различна стойност в същата минута НЕ е дубликат — това е отделно измерване. Възниква при изгубен
juggluco watermark или повторен `juggluco_pull.py --full`. Изкривява
TIR/mean/GMI без никакъв видим признак.

Употреба:
  python3 dedupe.py                  # само отчет, нищо не пипа (dry run)
  python3 dedupe.py --apply          # изтрива, пази записа с най-малък id
  DIABET_DB=/path/diabet.db python3 dedupe.py

Задължително направи backup преди --apply:
  cp data/diabet.db data/diabet.db.bak
"""
import os
import sqlite3
import sys

DB_PATH = os.environ.get("DIABET_DB", "/data/diabet.db")
APPLY = "--apply" in sys.argv


def main():
    if not os.path.exists(DB_PATH):
        print(f"[error] няма такава база: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    groups = conn.execute("""
        SELECT type, time, value, COALESCE(meta, '') AS m, COUNT(*) AS n, MIN(id) AS keep_id
        FROM entries
        GROUP BY type, time, value, COALESCE(meta, '')
        HAVING COUNT(*) > 1
        ORDER BY time
    """).fetchall()

    if not groups:
        total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        print(f"[ok] няма дубликати ({total} записа общо). Индексът може да се създаде.")
        conn.close()
        return

    extra = sum(g["n"] - 1 for g in groups)
    print(f"[info] {len(groups)} дублирани групи, {extra} записа за изтриване\n")

    # Показваме дали дубликатите имат различни стойности — ако да, изборът "най-малък id"
    # не е безобиден и трябва да се погледне на ръка.
    conflicting = 0
    for g in groups[:20]:
        rows = conn.execute(
            "SELECT id, value, notes FROM entries WHERE type=? AND time=? AND value=? "
            "AND COALESCE(meta, '')=? ORDER BY id",
            (g["type"], g["time"], g["value"], g["m"]),
        ).fetchall()
        vals = {r["value"] for r in rows}
        flag = ""
        if len(vals) > 1:
            conflicting += 1
            flag = "  <-- РАЗЛИЧНИ СТОЙНОСТИ"
        print(f"  {g['time']}  {g['type']:8s} n={g['n']}  "
              f"values={sorted(vals)}  keep id={g['keep_id']}{flag}")
    if len(groups) > 20:
        print(f"  ... и още {len(groups) - 20} групи")

    if conflicting:
        print(f"\n[warn] {conflicting} групи имат различни стойности на един и същ "
              f"timestamp — това не са CGM resend-и. Прегледай ги преди --apply.")

    if not APPLY:
        print("\n[dry run] нищо не е променено. Пусни с --apply за изтриване.")
        conn.close()
        return

    cur = conn.execute("""
        DELETE FROM entries
        WHERE id NOT IN (SELECT MIN(id) FROM entries GROUP BY type, time, value, COALESCE(meta, ''))
    """)
    conn.commit()
    print(f"\n[done] изтрити {cur.rowcount} записа")

    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_dedup "
            "ON entries(type, time, value, COALESCE(meta, ''))"
        )
        conn.commit()
        print("[done] UNIQUE(type,time,value,meta) индексът е създаден")
    except sqlite3.IntegrityError as e:
        print(f"[error] индексът пак не мина: {e}")
    conn.close()


if __name__ == "__main__":
    main()
