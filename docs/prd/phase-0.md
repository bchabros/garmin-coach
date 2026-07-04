# PRD — Garmin Coach · Faza 0: raw capture + idempotencja

> Status: **Ready for implementation (TDD)** · Data: 2026-07-04
> Źródła: `docs/garmin-coach-BUILD.md` §3/§4/§5/§8/§9/§10, `docs/schema.sql`
> Ten PRD zamyka decyzje wypracowane w sesji grillującej. Sekcja "Decyzje" jest wiążąca.

---

## 1. Cel

Zbudować deterministyczny, idempotentny backfill, który raz zasysa historię z Garmin
Connect (od `DATA_START_DATE`), zapisuje surowe payloady jako system-of-record i normalizuje
je do tabel core w SQLite. Fundament, na którym staną kolejne fazy (sync inkrementalny,
metryki, coach). **Bez** logiki metryk, retry, watermarku — to kolejne fazy.

## 2. Zakres

### W zakresie (Faza 0)
- `config.py` — pydantic-settings: `.env` + ścieżki + `DATA_START_DATE`.
- `client.py` — cienki transport: login z zapisanych tokenów / pełny login z MFA callback;
  wywołania `garminconnect`; mapowanie endpoint → realna metoda biblioteki.
- `db.py` — połączenie SQLite, bootstrap schematu z `src/garmin_coach/schema.sql`
  (`importlib.resources`), helpery upsert.
- `models.py` — czyste normalizery `payload dict → row dict` dla 6 streamów + `activity_sets`;
  mapowanie `gtype → discipline`.
- `sync.py` — backfill po zakresie dat: dla każdego dnia strzał → zapis `raw_payloads`
  (append) → normalizacja → upsert core. Bez retry/fallbacku.
- `cli.py` — `argparse`, subkomenda `backfill --from YYYY-MM-DD [--to YYYY-MM-DD]`.
- `schema.sql` przeniesiony do `src/garmin_coach/schema.sql` (źródło prawdy dla runtime).
- Testy TDD (fake client + fikstury), `.env.example`, `.gitignore`.

### Poza zakresem (odłożone do kolejnych faz)
- Faza 1: `sync_state`/watermark, retry/backoff, fallback per-dzień, izolacja padających
  streamów, komenda `sync`.
- Faza 2+: `features.py`, marty `daily_metrics`/`weekly_metrics`, coach skill, wykresy,
  raporty, kasety VCR.
- Streamy poza rdzeniem: `weight_log`, `race_predictions`, `fitness_markers`,
  `personal_records`, `body_battery_events` (tabele istnieją puste).

## 3. Decyzje (wiążące — z sesji grillującej)

| # | Decyzja |
|---|---------|
| D1 | Pełny `docs/schema.sql` jako źródło prawdy; ETL Fazy 0 wypełnia tylko rdzeń. |
| D2 | Surowe dane **wyłącznie** w tabeli `raw_payloads` (SQLite). Brak plików `raw/`. |
| D3 | „Bez duplikatów" (DoD) dotyczy tabel **core** (upsert). `raw_payloads` rośnie append-only — to cecha, nie bug. |
| D4 | Transport (`client.py`) rozdzielony od normalizacji (`models.py`). `sync.py` przyjmuje **wstrzykiwany klient** (protokół). Testy na fake clientach + fiksturach, zero sieci. |
| D5 | Fikstury JSON budowane z **realnych** payloadów pobranych przez MCP Garmina, zanonimizowanych. |
| D6 | **Poetry** jako manager. Minimalne deps Fazy 0, najnowsze stabilne wersje + lock. Nazwy metod `garminconnect` weryfikowane po instalacji (`dir(Garmin)`), nie zgadywane. |
| D7 | „Done" = kompletny kod + zielone testy (fake). Realny live-backfill (login+MFA) uruchamia **użytkownik** na końcu. |
| D8 | Czysta granica Faza 0/1. Backfill Fazy 0 bez retry/fallbacku — pierwszy strzał świadomie kruchy (raw zapisany częściowo → restart dokończy). |
| D9 | Backfill ciągnie 6 streamów: `activities`, `sleep`, `hrv_nightly`, `daily_wellness`, `training_readiness`, `training_status_daily` + `activity_sets` przy okazji aktywności. |
| D10 | `schema.sql` skopiowany do `src/garmin_coach/schema.sql`, ładowany `importlib.resources`; `docs/schema.sql` = snapshot dokumentacyjny. Test pilnuje zgodności obu. |
| D11 | `config.py`: `GARMIN_EMAIL` (wymagany), `GARMIN_PASSWORD` (opcjonalny), `DATA_START_DATE=2026-06-08`, `DB_PATH=./data/garmin.db`, `GARMINTOKENS=~/.garminconnect`. MFA jako wstrzykiwalny `prompt_mfa: Callable[[], str]` (domyślnie `input()`). Prymat zapisanych tokenów. |
| D12 | Zakres backfillu: `[DATA_START_DATE .. dziś − 1]` — „dziś" zawsze wykluczone. `has_data=0` tylko w `daily_wellness` (dzień z samymi null). Dla `sleep`/`hrv`/`readiness`/`status` brak danych = brak wiersza. Raw zapisywany zawsze (nawet payload z null). |
| D13 | CLI: `argparse`, tylko `backfill`. Entry point `garmin-coach` → `garmin_coach.cli:main` przez `[tool.poetry.scripts]`. |

## 4. Architektura / layout

```
src/garmin_coach/
  __init__.py
  config.py       # pydantic-settings
  client.py       # transport: login (+MFA), endpoint→method map, GarminClient protocol
  db.py           # connect, schema bootstrap, upsert helpers
  models.py       # normalizers payload→row, discipline mapping
  sync.py         # backfill(range) -> raw append + core upsert
  cli.py          # argparse: backfill
  schema.sql      # kopia docs/schema.sql (runtime source of truth)
tests/
  fixtures/       # anonimizowane realne payloady (z MCP)
  test_models.py  # normalizery + discipline mapping
  test_db.py      # bootstrap + upsert idempotency + raw append
  test_sync.py    # backfill: idempotencja core, has_data=0, zakres dat
  conftest.py     # in-memory DB, FakeGarminClient
data/garmin.db    # gitignored
.env.example
.gitignore
pyproject.toml    # [tool.poetry]
```

Deps runtime Fazy 0: `garminconnect`, `curl_cffi`, `pydantic`, `pydantic-settings`,
`python-dotenv`. Dev: `pytest`, `ruff`, `mypy`. (`pandas`/`numpy`/`matplotlib` i
`pytest-recording` dokładane w fazach, które ich realnie potrzebują.)

## 5. Specyfikacja zachowania

### 5.1 Backfill flow (`sync.backfill(client, db, from_date, to_date=None)`)
1. `to_date` domyślnie = dziś − 1. Iteruj dni `from_date..to_date` włącznie.
2. `activities`: strzał `get_activities_by_date(from, to)` (zakresowo) — mapuj każdą
   aktywność, upsert po `activity_id`; dla HIIT/strength wyciągnij `activity_sets`.
3. Dla każdego dnia per-stream (`sleep`, `hrv`, `wellness`, `readiness`, `status`):
   strzał → `raw_payloads` append (`endpoint`, `ref_date`, `fetched_at`, `payload`) →
   normalizacja → upsert po `date`.
4. `daily_wellness`: jeśli wszystkie istotne pola null → wiersz z `has_data=0`.
5. Wszystko w jednej transakcji per dzień (atomowość raw + core).

### 5.2 Idempotencja
- `activities` upsert po `activity_id`; dni po `date` (`INSERT ... ON CONFLICT DO UPDATE`).
- Dwa runy z rzędu ⇒ identyczna liczba wierszy w tabelach core.
- `raw_payloads` może urosnąć (nowy `fetched_at`) — to poprawne.

### 5.3 Normalizery (`models.py`)
- Czyste funkcje bez efektów ubocznych; wejście = dict z payloadu, wyjście = dict kolumn.
- Brakujące pola → `None` (nie wyjątek).
- `discipline`: `running→Bieganie` (trail jeśli nazwa/elewacja wskazują), `hiit→Hyrox/HIIT`,
  `strength_training→Siła`, `ski_touring/backcountry→Skitury`. Mapowanie w jednym miejscu.

## 6. Kryteria akceptacji (DoD)

- [ ] `garmin-coach backfill --from 2026-06-08` napełnia bazę z realnych danych (uruchamia użytkownik; login+MFA).
- [ ] Ponowne uruchomienie backfillu **nie zmienia liczby wierszy** w `activities`/`daily_wellness`/`sleep`/`hrv_nightly`/`training_readiness`/`training_status_daily`.
- [ ] Surowe payloady obecne w `raw_payloads` (po jednym+ wpisie na strzał).
- [ ] `daily_wellness.has_data=0` dla dni z samymi null; brak wiersza dla pustych sleep/hrv/readiness.
- [ ] „Dziś" nigdy nie pobierane.
- [ ] Wszystkie testy TDD zielone bez dostępu do sieci.
- [ ] `ruff` i `mypy` czyste.

## 7. Plan testów (TDD)

| Plik | Czerwone testy (kolejność) |
|------|----------------------------|
| `test_models.py` | normalizacja activity (pola TE/load/HR), sleep, hrv, wellness, readiness, status; mapowanie discipline; brakujące pola → None |
| `test_db.py` | bootstrap schematu z `importlib.resources`; upsert activity idempotentny; upsert dnia idempotentny; raw append tworzy nowy wiersz |
| `test_sync.py` | backfill z FakeClient napełnia core; **drugi run nie tworzy duplikatów**; `has_data=0` dla payloadu z null; zakres wyklucza dziś; raw zapisany dla każdego strzału |
| `test_schema_sync.py` | `src/garmin_coach/schema.sql` == `docs/schema.sql` (guard rozjazdu) |

FakeGarminClient: implementuje protokół `client.py`, zwraca fikstury z `tests/fixtures/`
per (endpoint, date). Symuluje dzień pusty (null payload) dla testu `has_data`.

## 8. Ryzyka / otwarte

- **Kruchość backfillu (D8):** pierwszy live-run bez retry może paść na timeout Garmina.
  Mitygacja: raw zapisany transakcyjnie per dzień → restart dokończy od miejsca przerwania.
  Pełna odporność w Fazie 1.
- **Nazwy metod `garminconnect`:** zweryfikować po `poetry install` (D6) — mapa endpoint→metoda
  może wymagać korekt względem BUILD (ufamy kodowi, nie dokumentowi).
- **Anonimizacja fikstur:** usunąć `userProfilePK`, e-mail, tokeny, geolokalizację przed
  zapisem do repo.
