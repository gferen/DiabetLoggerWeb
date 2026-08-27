# Diabet Logger

Self-hosted проследяване на кръвна захар, инсулин, кетони и структурирани данни за диабет (Тип 1). FastAPI + SQLite + Chart.js, без external зависимости от cloud услуги.

![status](https://img.shields.io/badge/status-active-brightgreen)
![python](https://img.shields.io/badge/python-3.12-blue)
![license](https://img.shields.io/badge/license-private-lightgrey)

---

## Какво прави

- **Логване:** BG (mmol/L), Бърз/Бавен инсулин (U), Кетони (mmol/L), Хранене (XE калкулатор)
- **Структурирани данни:** физическа активност, алкохол, смяна на устройство (ампула/сензор) — с отделен timestamp за начало на хранене vs. момент на инжектиране
- **CGM автоматична синхронизация** от Juggluco (Libre) — cron на 24ч или ръчен бутон
- **Клинични метрики:** Time-in-Range %, GMI, средна BG, SD, CV%
- **Интерактивна графика** с time-range филтри и zoom/pan
- **Настройваем XE коефициент** (грамове въглехидрати на хлебна единица)
- **Login** (JWT) или API Key auth — опционално
- **Export/Import** JSON и CSV
- **Multi-device sync** — една база, auto-refresh

## Технологии

| Слой | Технология |
|---|---|
| Backend | FastAPI, SQLite, PyJWT |
| Frontend | Vanilla JS, Chart.js, без build стъпка |
| CGM интеграция | Juggluco webserver API (Libre sensors) |
| Deploy | Docker Compose |

## Quick start

```bash
git clone <repo>
cd diabet-logger
docker compose up -d --build
```

Отвори `http://<server>:8765/`. Без auth по подразбиране (препоръчва се зад WireGuard/VPN).

## Auth (опционално)

```bash
# .env
DIABET_USERNAME=потребител
DIABET_PASSWORD=силна-парола
DIABET_API_KEY=random-string-за-скриптове
DIABET_TZ=Europe/Sofia
```

## CGM синхронизация (Juggluco)

`juggluco_pull.py` тегли данни от Juggluco's вграден webserver (path-based `api_secret` auth) и ги записва през `/api/entries`. Изисква мрежов достъп до телефона (WireGuard препоръчително). Виж коментарите в скрипта за пълна конфигурация.

```bash
python3 juggluco_pull.py            # incremental
python3 juggluco_pull.py --full     # пълен backfill от активиране на сензора
```

## API документация

Пълен Swagger UI на `/docs`. Основни endpoints:

| Method | Path | Описание |
|---|---|---|
| GET | `/api/entries` | записи, филтруеми по `type`/`from`/`to`/`limit` |
| POST | `/api/entries` | нов запис (структурирани полета: `meal_time`, `carbs_g`, `meta`) |
| POST | `/api/cgm-sync` | ръчна CGM синхронизация |
| GET | `/api/stats` | TIR/GMI/mean/SD/CV |
| GET/PUT | `/api/settings` | XE коефициент и др. |
| GET | `/api/export` | пълен JSON dump |
| POST | `/api/import` | merge/replace import |

## Референтни диапазони

**BG (mmol/L):** `<3.0` много ниско · `3.0-3.8` ниско · `3.9-10.0` в норма · `10.1-13.9` високо · `>13.9` много високо

**Кетони (β-хидроксибутират, mmol/L):** `<0.6` нормални · `0.6-1.5` повишени · `1.5-3.0` риск от DKA · `>3.0` спешно

**TIR цели (T1D):** in-range >70%, <3.0 mmol/L <1%, CV <36%

⚠️ Не е медицинско устройство. GMI е оценка, не заменя лабораторно HbA1c.

## Backup

```bash
cp data/diabet.db backup/diabet-$(date +%F).db
# препоръчан cron: всеки ден в 03:00
```

## Migration/дедупликация

```bash
python3 dedupe.py          # dry-run отчет
python3 dedupe.py --apply  # изтрива потвърдени дубликати, създава UNIQUE индекс
```

## Тестове

```bash
python3 qa.py   # 150+ теста: калкулации, auth, миграция, structured data round-trip
```

## Лиценз

Личен проект, не за публична дистрибуция без съгласие.
