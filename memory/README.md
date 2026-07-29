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

Pod tytułem `athlete-profile.md` stoi linia z datą ostatniej aktualizacji: zaczyna się od
`_Ostatnia aktualizacja:`, dalej `RRRR-MM-DD`, a za datą zwykle własna notka o tym, jaki
stan danych profil streszcza. Stąd skill coacha zna wiek profilu — przy dopisku podmienia
samą datę, notka zostaje taka, jak ją napisałeś. Bez tej linii nie da się stwierdzić, że
profil się zestarzał, więc `tests/test_coach_skill_profile.py` pilnuje jej po obu stronach:
ręczna edycja, która ją gubi, wywala `task check`, zamiast psuć się po cichu.

## Co robi z tym plikiem skill coacha

Źródłem prawdy jest sekcja „The athlete profile” w `skills/coach/SKILL.md`: tam stoi, kiedy
profil jest czytany, jak oceniany jest jego wiek i jak wygląda propozycja poprawki. Dwie
rzeczy warto mieć w głowie, otwierając ten folder:

- **liczby zawsze z bazy** — profil daje kontekst jakościowy, nigdy samą wartość;
- **zapis tylko za zgodą** — skill pokazuje dokładne linijki i czeka na wyraźne „tak”;
  odmowa zostawia plik bajt w bajt taki sam.
