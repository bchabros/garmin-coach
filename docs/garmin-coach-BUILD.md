# garmin-coach — brief budowy (pod Claude Code)

> Dokument wykonawczy. Odczytaj całość, potem realizuj **fazami** (0 → 5). Każda faza ma
> *Definition of Done*; nie przechodź dalej, dopóki DoD nie jest spełnione. Kod w Pythonie.
> Komentarze/docstringi po angielsku, commit messages po angielsku.
>
> **STATUS: fazy 0–5 ukończone.** Ten dokument jest zapisem historycznym budowy
> (spec + statusy per faza poniżej). Dalszy rozwój żyje w **`docs/ROADMAP.md`**
> (fazy 6+, jedno źródło łączące potrzeby z sesji coachingowych i przegląd rynku).

---

## 0. Cel i zasada naczelna

Zbudować lokalny system, który raz dziennie ściąga dane z Garmin Connect, trzyma je w bazie
jako *system-of-record*, liczy metryki treningowe (HRV baseline, ACWR, rozkład obciążenia) i
pozwala agentowi-„coachowi" generować cotygodniowy raport: *czy trening idzie dobrze i czego
brakuje*.

**Zasada naczelna — rozdziel transport od inteligencji:**

- **Deterministyczny ETL** (ściąganie + normalizacja) idzie przez bibliotekę
  [`garminconnect`](https://github.com/cyberjunky/python-garminconnect), uruchamiany cyklicznie.
  **Nie** przez MCP — MCP Garmina bywa niestabilny (timeouty na RHR, payloady >1 MB), nadaje się
  do ad-hoc eksploracji przez agenta, nie do pipeline'u.
- **Warstwa metryk + coach** czyta gotową bazę, nigdy nie strzela do Garmina „w locie".

To **jedno repo** i **dwie powierzchnie pracy**, nie dwa projekty:

```
                       ┌─────────────────────────────────────────┐
   Garmin Connect ───► │  garmin-coach (jedno repo)               │
   (garminconnect)     │                                          │
                       │  sync.py  → raw/ + SQLite (system-of-record)
                       │  features.py → daily_metrics (mart)      │
                       │  skills/coach/SKILL.md                   │
                       └───────────────┬──────────────┬───────────┘
                                       │              │
                        Claude Code ◄──┘              └──► Claude Cowork
                     (buduje/utrzymuje repo,      (wskazuje na to samo repo/DB,
                      migracje, backfille)         odpala SKILL.md → raport+wykresy)
```

Cowork **nie** dostaje własnej kopii logiki — reużywa `features.py` i czyta tę samą bazę. Gdyby
to były dwa repo, logika metryk się zduplikuje i rozjedzie.

---

## 1. Decyzje technologiczne

| Obszar | Wybór | Uzasadnienie / kiedy zmienić |
|---|---|---|
| Pobieranie | `garminconnect` + `curl_cffi` | oficjalny wrapper, auto-refresh tokenów, MFA |
| Manager środowiska | `uv` (albo `pdm`) | szybki, lockfile; `uv run ...` do zadań |
| System-of-record | **SQLite** | transakcyjny, w repo/backupie, Claude Code czyta natywnie |
| Warstwa analityczna | **DuckDB — dopiero gdy zaboli** | window functions nad tym samym plikiem; nie dokładaj od razu |
| Surowe dane | JSON w `raw/{date}/{endpoint}.json` **i/lub** tabela `raw_payloads` | reprocessing bez ponownego strzału do Garmina |
| Wykresy | `matplotlib` | te same co w analizie wyjściowej |
| Config/sekrety | `.env` (pydantic-settings) + tokeny w `~/.garminconnect` | patrz sekcja 8 |

Zależności startowe: `garminconnect`, `curl_cffi`, `pandas`, `numpy`, `matplotlib`,
`pydantic`, `pydantic-settings`, `python-dotenv`; dev: `pytest`, `pytest-recording` (VCR),
`ruff`, `mypy`.

---

## 2. Layout repozytorium

```
garmin-coach/
├── pyproject.toml
├── .env.example                 # EMAIL, PASSWORD (opcjonalnie), DATA_START_DATE, DB_PATH
├── .gitignore                   # .env, raw/, *.db, ~/.garminconnect NIE commitować
├── README.md
├── data/
│   └── garmin.db                # SQLite (gitignored; backup osobno)
├── raw/                         # surowe JSON-y per dzień/endpoint (gitignored)
├── src/garmin_coach/
│   ├── __init__.py
│   ├── config.py                # pydantic-settings: env + ścieżki
│   ├── client.py                # login, retry/backoff, wrapper na garminconnect
│   ├── db.py                    # połączenie, migracje, upsert helpers
│   ├── schema.sql               # DDL (sekcja 4)
│   ├── sync.py                  # ETL inkrementalny (sekcja 5)
│   ├── models.py                # dataclasses/pydantic: Activity, DailyWellness, Sleep, HrvNight
│   ├── features.py              # metryki → daily_metrics (sekcja 6)
│   ├── report.py                # wykresy + tekstowy raport tygodniowy
│   └── cli.py                   # `garmin-coach sync|backfill|features|report`
├── skills/
│   └── coach/
│       └── SKILL.md             # skill dla Cowork (sekcja 7)
├── tests/
│   ├── cassettes/               # VCR
│   ├── test_sync.py
│   └── test_features.py
└── scripts/
    └── daily.sh                 # cron/launchd entrypoint: sync → features
```

---

## 3. Fazowanie (każda faza działa samodzielnie)

### Faza 0 — raw capture + idempotencja
- `client.py`: login z zapisem tokenów, `example.py`-style. Obsłuż MFA callback.
- `sync.py`: dla zadanego zakresu dat ściągnij i **zapisz surowe JSON-y** do `raw/`, potem
  znormalizuj i **upsert** do `activities` / `daily_wellness` / `sleep` / `hrv_nightly`.
- Backfill jednorazowy od `DATA_START_DATE` (u tego użytkownika realne dane od **2026-06-08**;
  wcześniej onboarding/pусто — zapisuj jako jawny „brak", nie NULL-mgłę).
- **DoD:** `garmin-coach backfill --from 2026-06-08` napełnia bazę; ponowne uruchomienie nie
  tworzy duplikatów (upsert po kluczu); surowe JSON-y leżą w `raw/`.

- **✅ STATUS: DONE (2026-07-04).** Zrealizowane test-first (22 testy, `ruff`+`mypy` czyste).
  DoD potwierdzone na żywej bazie: 26 dni (2026-06-08→07-03, „dziś" wykluczone), 15 aktywności,
  drugi run bez zmian w core (`raw_payloads` rośnie append-only). **Odchylenia od briefu**
  (świadome decyzje — patrz `docs/prd/phase-0.md`): surowe dane w tabeli `raw_payloads` (nie
  pliki `raw/`), manager **Poetry** (nie `uv`), backfill ciągnie **6 streamów** (dodano
  `training_readiness` + `training_status_daily`). **Odłożone:** `activity_sets`.

### Faza 1 — sync inkrementalny + odporność
- Tabela `sync_state` (watermark per stream). Sync ciągnie tylko `[watermark+1 .. wczoraj]`.
- Retry z exponential backoff; **fallback z zakresu na per-dzień** dla endpointów, które
  timeoutują na całym zakresie (dokładnie problem, który wystąpił ze snem i RHR w MCP).
- Endpoint, który padnie po retry, loguje ostrzeżenie i **nie** blokuje reszty streamów.
- **DoD:** `garmin-coach sync` odpalony dwa razy pod rząd jest idempotentny; padnięcie jednego
  streamu nie wywala całego runu; watermark się przesuwa.

- **✅ STATUS: DONE.** Watermark per stream w `sync_state`, retry z backoffem, fallback
  per-dzień, izolacja streamów. Decyzje: `docs/prd/phase-1.md` +
  `docs/adr/0001-phase-1-incremental-sync.md`; testy seamowe w `tests/test_sync.py`.

### Faza 2 — warstwa metryk (mart)
- `features.py` liczy i materializuje do `daily_metrics` (definicje w sekcji 6):
  HRV baseline (mediana krocząca) + SD + flaga `< baseline − 1·SD`; ACWR (acute7/chronic28) +
  `n_chronic` (ile dni realnie w oknie); kubełki load balance po TE; minuty w strefach HR z
  `hr_z1..z5`.
- **DoD:** `garmin-coach features` odtwarza wyniki z analizy referencyjnej dla 09.06–04.07
  (baseline ≈ 68 ms, SD ≈ 11 ms, próg ≈ 57 ms; ACWR na 03.07 ≈ 1.0, ref. Garmin 1.1).

- **✅ STATUS: DONE.** `features.py` materializuje mart `daily_metrics`; złota regresja w
  `tests/test_features.py`. Decyzje: `docs/prd/phase-2.md` +
  `docs/adr/0002-phase-2-metrics-semantics.md`.

### Faza 3 — skill „coach"
- `skills/coach/SKILL.md` (konwencja skilli — Twoja działka): enkapsuluje **reguły** (sekcja 7),
  czyta `daily_metrics`, zwraca raport + wykresy. Agent nie „myśli od zera".
- **DoD:** w Cowork „przejrzyj mój ostatni tydzień" produkuje raport tekstowy + 2 wykresy
  (HRV z pasmem ±1 SD, ACWR w czasie) i listę konkretnych sygnałów.

- **✅ STATUS: DONE.** Deterministyczny silnik (`digest.py`/`signals.py` →
  `garmin-coach report`) buduje `reports/{date}/digest.json` + 2 wykresy; skill pisze
  `report.md` wyłącznie z digestu (nigdy z surowego martu, nigdy z Garmina). Sygnały 1–5
  z sekcji 7; reguła 6 (plan vs actual) przesunięta do Fazy 5. Decyzje:
  `docs/prd/phase-3.md` + `docs/adr/0003-phase-3-coach-signals.md`; złota regresja w
  `tests/test_digest.py`.

### Faza 4 — automatyzacja
- `scripts/daily.sh` (sync → features) pod cron/launchd. Cotygodniowy review z Cowork.
- Alerty (progi z reguł): rano HRV < próg → „zdegraduj sesję jakościową"; ACWR > 1.3 → „rozważ
  deload"; `AEROBIC_LOW_SHORTAGE` → „dołóż Z2".
- **DoD:** nocny run działa bez interakcji; log rotowany; błąd = niezerowy exit + wpis w logu.

- **✅ STATUS: DONE.** Seam `daily.run_daily(client, conn, ...) → DailyResult`
  (sync → features → digest, bez wykresów na nocnej ścieżce); alerty = sygnały
  warn/alert z digestu; kontrakt exit: `ok`/0, `degraded`/1, `failed`/2.
  `garmin-coach daily` + cienki `scripts/daily.sh` + przykładowy plist launchd;
  logowanie przez `RotatingFileHandler`. Decyzje: `docs/prd/phase-4.md` +
  `docs/adr/0004-phase-4-automation.md`; testy w `tests/test_daily.py`.

### Faza 5 — domknięcie pętli
- Plan-vs-actual (szablon tygodnia użytkownika kontra faktyczne logi), detekcja deloadu,
  trendy VO2max/próg, multi-sport gdy wróci sezon skiturowy (`discipline` już jest w schemacie).
- **DoD:** raport pokazuje rozjazd „plan vs realizacja" i wykrywa „dwa twarde dni z rzędu".

- **✅ STATUS: DONE.** `weekly.py` → mart `weekly_metrics` (tylko pełne tygodnie
  pon–ndz); plan-vs-actual (`plan_adherence` vs `plan_template`), Foster
  `monotony`/`strain`, `max_consec_hard`, nowy sygnał `DELOAD_ADVISED`; digest zyskał
  sekcję `weekly`, raport „Tydzień: plan vs realizacja". Decyzje: `docs/prd/phase-5.md`
  + `docs/adr/0005-phase-5-weekly-rollups-and-plan-vs-actual.md`; testy w
  `tests/test_weekly.py` + `tests/test_signals.py`. Trendy VO2max/próg, multi-sport
  weighting i eksport PDF/Notion odłożone → `docs/ROADMAP.md`.

---

## 4. Schemat bazy (medaliony: raw → core → mart)

`schema.sql` — utwórz idempotentnie (`CREATE TABLE IF NOT EXISTS`). Klucze i upserty krytyczne
dla idempotencji.

```sql
-- RAW (append-only, nigdy nie nadpisuj) -----------------------------------
CREATE TABLE IF NOT EXISTS raw_payloads (
  fetched_at  TEXT NOT NULL,          -- ISO timestamp pobrania
  endpoint    TEXT NOT NULL,          -- np. 'get_sleep_data'
  ref_date    TEXT NOT NULL,          -- data, której dotyczy
  payload     TEXT NOT NULL,          -- surowy JSON
  PRIMARY KEY (endpoint, ref_date, fetched_at)
);

-- CORE (znormalizowane, upsert) -------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
  activity_id   INTEGER PRIMARY KEY,  -- dedup po activityId
  start_local   TEXT NOT NULL,
  gtype         TEXT NOT NULL,        -- running | hiit | strength_training | ...
  discipline    TEXT,                 -- Bieganie | Hyrox/HIIT | Siła | Skitury | Trail
  dur_s         REAL,
  distance_m    REAL,
  aero_te       REAL,
  anaero_te     REAL,
  training_load REAL,                 -- activityTrainingLoad
  te_label      TEXT,                 -- AEROBIC_BASE | TEMPO | LACTATE_THRESHOLD | VO2MAX
  avg_hr        INTEGER,
  max_hr        INTEGER,
  hr_z1_s REAL, hr_z2_s REAL, hr_z3_s REAL, hr_z4_s REAL, hr_z5_s REAL,
  name          TEXT
);

CREATE TABLE IF NOT EXISTS daily_wellness (
  date          TEXT PRIMARY KEY,
  rhr           INTEGER,
  acute_load    REAL,                 -- Garmin acuteLoad (najnowszy ts/dzień)
  chronic_load  REAL,                 -- z training_status, jeśli dostępne
  garmin_acwr   REAL,                 -- referencyjne ACWR Garmina
  bb_min INTEGER, bb_max INTEGER,     -- body battery
  steps         INTEGER,
  training_status TEXT,               -- PRODUCTIVE | MAINTAINING | ...
  has_data      INTEGER DEFAULT 1     -- 0 = jawny brak (onboarding/nie noszony)
);

CREATE TABLE IF NOT EXISTS sleep (
  date        TEXT PRIMARY KEY,
  total_s     INTEGER, deep_s INTEGER, rem_s INTEGER, light_s INTEGER, awake_s INTEGER,
  score       INTEGER,
  resting_hr  INTEGER
);

CREATE TABLE IF NOT EXISTS hrv_nightly (
  date        TEXT PRIMARY KEY,
  avg_hrv     INTEGER,                -- lastNightAvg
  weekly_avg  INTEGER,
  status      TEXT                    -- BALANCED | LOW | NONE(onboarding) ...
);

CREATE TABLE IF NOT EXISTS sync_state (
  stream          TEXT PRIMARY KEY,   -- 'activities' | 'sleep' | 'hrv' | 'wellness'
  last_synced_date TEXT NOT NULL
);

-- MART (policzone; nadpisywalne przy każdym `features`) --------------------
CREATE TABLE IF NOT EXISTS daily_metrics (
  date            TEXT PRIMARY KEY,
  hrv             INTEGER,
  hrv_baseline    REAL,               -- mediana krocząca
  hrv_sd          REAL,
  hrv_low_flag    INTEGER,            -- 1 jeśli hrv < baseline - 1*SD
  load_day        REAL,               -- suma training_load aktywności dnia
  acwr            REAL,               -- acute7/chronic28 (własne)
  n_chronic       INTEGER,            -- ile dni w oknie chronic (wiarygodność!)
  load_low        REAL,               -- kubełki load balance
  load_high       REAL,
  load_anaerobic  REAL,
  z1_min REAL, z2_min REAL, z3_min REAL, z4_min REAL, z5_min REAL,
  sleep_score     INTEGER,
  rhr             INTEGER
);
```

Upsert w SQLite: `INSERT ... ON CONFLICT(<pk>) DO UPDATE SET ...`. Dni bez danych zapisuj z
`has_data=0`, nie pomijaj — inaczej luki są nierozróżnialne od „jeszcze nie pobrano".

---

## 5. Reguły implementacyjne ETL (`sync.py`)

- **Inkrementalność:** czytaj `sync_state`, ciągnij `[last_synced_date+1 .. wczoraj]`. „Dzisiaj"
  pomijaj — dane niekompletne (HRV/sen dostępne po nocy).
- **Idempotencja:** aktywności upsert po `activity_id`; dni po `date`. Dwa runy = ten sam stan.
- **Retry/backoff:** owiń każde wywołanie API w retry (np. 3 próby, backoff 2^n s). Na wyjątku
  po ostatniej próbie: log WARN, zapisz `has_data=0`/pomiń dzień, **kontynuuj** inne streamy.
- **Fallback per-dzień:** endpointy z metodami zakresowymi (sen, HRV) potrafią timeoutować na
  szerokim oknie — jeśli zakres padnie, spadnij na pętlę per-dzień. (To realny problem z tego
  konta: sen >1 MB na zakresie, RHR timeoutował.)
- **Raw first:** najpierw zapis surowego JSON do `raw_payloads`/`raw/`, potem normalizacja. Gdy
  normalizacja się zmieni, reprocessujesz z raw bez ruszania Garmina.
- **Onboarding/NULL:** rekordy z samymi `null` (jak maj–początek czerwca) → `has_data=0`.
- **Dyscyplina:** mapuj `gtype`: `running`→`Bieganie` (chyba że nazwa/elewacja wskazują trail →
  `Trail`), `hiit`→`Hyrox/HIIT`, `strength_training`→`Siła`, `ski_touring`/`backcountry`→
  `Skitury`. Trzymaj mapowanie w jednym miejscu (`models.py`), łatwe do rozszerzenia.

---

## 6. Definicje metryk (`features.py`) — spec z analizy referencyjnej

> Te definicje odtwarzają analizę zrobioną ręcznie w tym wątku. Trzymaj się ich, żeby wyniki
> były porównywalne.

**HRV baseline / flaga**
- `hrv_baseline` = mediana krocząca `avg_hrv` (okno konfigurowalne; domyślnie całe dostępne okno
  albo 60 nocy, gdy uzbiera się historia). `hrv_sd` = odchylenie std (ddof=1) na tym samym oknie.
- `hrv_low_flag = 1` gdy `avg_hrv < hrv_baseline − 1·hrv_sd`.
- Ref.: 09.06–04.07 → baseline ≈ 68 ms, SD ≈ 11 ms, próg ≈ 57 ms; flagowane 11.06, 18.06, 19.06,
  27.06, 04.07.

**ACWR (własne)**
- `load_day` = suma `training_load` aktywności danego dnia (0 jeśli brak).
- `acute7` = średnia dzienna `load_day` z 7 dni; `chronic28` = średnia dzienna z 28 dni.
- `acwr = acute7 / chronic28`. Flaga ryzyka > 1.5, detreningu < 0.8, strefa OK 0.8–1.3.
- **`n_chronic`** = liczba dni realnie w oknie chronic. **Krytyczne:** dopóki `n_chronic < 28`,
  ACWR jest zawyżony/orientacyjny — raport MUSI to oznaczać (ostrzeżenie na wykresie i w tekście).
  Gdy jest, dokładaj `garmin_acwr` z `daily_wellness` jako punkt odniesienia.

**Kubełki load balance (logika Garmina — po TE, nie po strefach HR)**
- `load_anaerobic` += `training_load` gdy `anaero_te ≥ 1.0`.
- w przeciwnym razie: `load_low` gdy `aero_te < 2.5`, inaczej `load_high`.
- Cele miesięczne bierz z `get_training_status` (`monthlyLoad*Target*`) i porównuj — to źródło
  komunikatu `AEROBIC_LOW_SHORTAGE`.

**Strefy HR (osobno od kubełków!)**
- `z1..z5_min` = suma `hr_z1..z5_s`/60 z aktywności dnia. To minuty w strefach tętna — inny
  „język" niż kubełki load balance. Raport pokazuje oba obok siebie, bo odpowiadają na różne
  pytania (rozkład czasu vs rozkład bodźca).

---

## 7. Skill „coach" (`skills/coach/SKILL.md`)

**Wejście:** `daily_metrics` (+ `activities`, `daily_wellness`) za wskazany zakres (domyślnie
ostatnie 7/28 dni). **Wyjście:** zwięzły raport tekstowy + 2 wykresy (HRV z pasmem ±1 SD, ACWR
w czasie), zapisane do `reports/{data}/`.

Reguły do zaszycia (progi konfigurowalne, wartości domyślne z tego użytkownika):

1. **Rozkład intensywności** — jeśli `load_low` poniżej celu i `load_high` powyżej → sygnał
   „za dużo szarej strefy, dołóż Z2" (`AEROBIC_LOW_SHORTAGE`).
2. **ACWR** — poza 0.8–1.3 flaguj; ale gdy `n_chronic < 28`, dopisz „wartość orientacyjna".
3. **HRV** — noce `hrv_low_flag=1`; jeśli rano < próg → rekomendacja „zdegraduj dzisiejszą sesję
   jakościową do easy".
4. **Dwa twarde dni z rzędu** — wykryj kolejne dni z wysokim `load_day`/`anaero_te` bez buforu
   (u tego użytkownika ryzyko: stos piątek→sobota).
5. **Sen** — koreluj `hrv` z `sleep_score` (w danych ref. r ≈ 0.57); najgorsze HRV bywa napędzane
   snem, nie treningiem — nie myl przyczyn.
6. **Plan vs actual** (Faza 5) — szablon tygodnia użytkownika:
   pon rest · wt FBB+Hyrox · śr easy/long run · czw rest · pt Crossfit+Hyrox · sob Hyrox/tempo ·
   ndz czasem easy/long. Pokaż rozjazd.

Ton raportu: konkret, liczby, bez lania wody; zastrzeżenie że to czytanie danych, nie zalecenie
medyczne/trenerskie.

---

## 8. Sekrety, auth, prywatność

- **Auth:** `garminconnect` używa mobilnego SSO. Pierwszy login zapisuje tokeny do
  `~/.garminconnect/garmin_tokens.json` (mode 0600), potem auto-refresh — pełny re-login tylko
  gdy refresh token wygaśnie/zostanie odwołany. MFA: przekaż `prompt_mfa` callback.
- **Hasło:** najlepiej **nie** trzymać w `.env` — poleguj na zapisanych tokenach po pierwszym
  logowaniu. Jeśli już, to `.env` (gitignored), nigdy w repo.
- **.gitignore:** `.env`, `raw/`, `*.db`, `reports/`, wszelkie tokeny. Do repo idą tylko kod,
  `schema.sql`, `SKILL.md`, `.env.example`.
- **Dane zdrowotne są wrażliwe** — baza i backupy trzymaj lokalnie/prywatnie.

---

## 9. Testy

- `pytest` + VCR (`pytest-recording`): nagraj kasety odpowiedzi Garmina raz, potem testy grają
  z `tests/cassettes/` bez sieci. **Zanonimizuj** kasety (usuń tokeny, e-mail, `userProfilePK`).
- `test_features.py`: na fiksturze 09.06–04.07 assert baseline≈68, SD≈11, próg≈57, ACWR(03.07)≈1.0,
  poprawne flagi HRV i kubełki load. To złota próbka regresyjna.
- `test_sync.py`: idempotencja upsertów; fallback per-dzień po symulowanym timeout zakresu;
  `has_data=0` dla payloadu z samymi null.

---

## 10. Pułapki (przeczytaj zanim zaczniesz)

- **Nazwy metod:** biblioteka ma 130+ metod. **Nie zgaduj** — odczytaj realne sygnatury z
  `demo.py` i źródeł pakietu (`python -c "import garminconnect, inspect; print([m for m in dir(garminconnect.Garmin) if not m.startswith('_')])"`). Zmapuj potrzebne: stats/summary, heart
  rates/RHR, sleep, HRV, training status, training readiness, activities-by-date, body battery.
- **RHR bywa zawodny** — miej fallback per-dzień; RHR jest też w payloadzie snu (`restingHeartRate`),
  możesz go stamtąd wyciągnąć zamiast osobnego strzału.
- **Zakresy snu/HRV** potrafią przekraczać limity — patrz fallback per-dzień.
- **Onboarding:** to konto ma realne dane od **2026-06-08**; nie interpretuj wcześniejszych
  zer/null jako „zero aktywności".
- **ACWR bez pełnego chronic (28 dni)** jest niewiarygodny — zawsze raportuj `n_chronic`.
- **Wersje:** `garminconnect` v0.3.4 (maj 2026) w chwili pisania; jeśli API/metody się zmieniły,
  zaufaj `demo.py` z zainstalowanej wersji, nie temu dokumentowi.

---

## 11. Pierwsze uruchomienie (kolejność komend)

```bash
# środowisko
uv venv && source .venv/bin/activate
uv pip install --upgrade garminconnect curl_cffi pandas numpy matplotlib \
    pydantic pydantic-settings python-dotenv
uv pip install --group dev pytest pytest-recording ruff mypy   # lub w pyproject

# pierwszy login (zapisze tokeny do ~/.garminconnect) — obsłuż MFA jeśli włączone
python -c "from garmin_coach.client import login; login()"

# backfill + metryki + raport
garmin-coach backfill --from 2026-06-08
garmin-coach features
garmin-coach report --last 28

# dalej: nocny run
scripts/daily.sh   # sync -> features ; podepnij pod cron/launchd
```

---

## 12. Backlog / rozszerzenia (po Fazie 5)

- DuckDB jako warstwa analityczna nad `garmin.db`, gdy SQL okien zrobi się ciężki.
- Trendy długoterminowe: VO2max, próg mleczanowy, race predictions (są w API).
- Multi-sport i sezon skiturowy (`discipline=Skitury`) — schemat już gotowy.
- Eksport raportu do PDF/Notion (użytkownik już żyje w Notion).
- Wersjonowanie `daily_metrics` gdy zmienisz definicje metryk (kolumna `features_version`).
```
