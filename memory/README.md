# memory/

Pamięć długoterminowa o zawodniku — kontekst jakościowy, który **nie mieści się w bazie**
(cele, tendencje, decyzje coachingowe, preferencje, otwarte wątki). Baza SQLite trzyma
liczby; ten folder trzyma kontekst relacji trener–zawodnik, żeby każda nowa sesja mogła
wrócić do wątku bez zaczynania od zera.

Zasady:
- Pliki `.md`, po polsku, czytane na starcie sesji i dopisywane, gdy pojawiają się fakty.
- Liczby zawsze pochodzą z bazy/Garmina — profil je streszcza, nie zastępuje.
- Jeden fakt = jedno miejsce; przy sprzątaniu scalać duplikaty i poprawiać nieaktualne.

Pliki:
- `athlete-profile.md` — główny profil (cel, fizjologia, tendencje, decyzje, preferencje).

Ten README jest w repo; reszta folderu nie — profil to prywatne dane zawodnika.

## Kontrakt: linia daty

Pierwsza linia `athlete-profile.md` to `_Ostatnia aktualizacja: RRRR-MM-DD._` — stąd skill
coacha zna wiek profilu. Bez niej nie ma jak stwierdzić, że profil się zestarzał, więc
`tests/test_coach_skill_profile.py` pilnuje jej po obu stronach: ręczna edycja, która gubi
tę linię, wywala `task check`, zamiast psuć się po cichu.

## Co robi z tym plikiem skill coacha

Sekcja „The athlete profile" w `skills/coach/SKILL.md`:

- czyta profil, zanim doradzi w którymkolwiek flow — raport, plan tygodnia, autorstwo
  treningu; brak pliku mówi wprost, zamiast zmyślać kontekst;
- liczby bierze wyłącznie z bazy — profil daje kontekst jakościowy, nigdy wartość;
- mówi, gdy profil jest starszy niż trzy tygodnie albo gdy „Otwarte wątki" mają wpis po
  terminie;
- nazywa sprzeczność, gdy profil twierdzi coś, czemu przeczy digest (data zawodów, blok,
  strefy) — wygrywa digest;
- dopisek proponuje i pokazuje linijka po linijce, a zapisuje dopiero po wyraźnej zgodzie.
