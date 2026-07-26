# Plain Language

How to write when talking to the athlete **about the work**: grilling questions, PRD prose,
ticket bodies, and chat explanations. The athlete owns the domain, not the schema — a
sentence they cannot answer without opening the code is a broken sentence.

This is about plainness, not about which language. Chat and grilling follow the language of
the question; PRDs, tickets, and issues stay English (see the table at the bottom). Both have
to be plain.

It does not touch code. `code-style.md` still governs what goes inside the source.

## Say what it does, not what it is called

An identifier is a **locator, not a sentence subject**. Say what the thing does in domain
terms, then hand over the identifier in backticks in parentheses so it can be found in
`digest.json`, `docs/glossary.md`, or the DB.

Bad — a sentence assembled out of five field names:

> the recommended `intended_type` with its `intensity_cap` and `pace_target_s_per_km`, and
> whether it is a downgrade from `planned_intent`

Good — the same content, readable in one pass:

> which session to do tomorrow, how hard to go (a heart-rate ceiling), the target pace, and —
> when it is easier than what the plan asked for — that it is a step down
> (`intended_type`, `intensity_cap`, `pace_target_s_per_km`, `planned_intent`)

**Once per section, not once per document.** The parenthetical earns its place on a term's
first appearance in each section, then the plain name stands alone. Nobody reads a PRD top to
bottom, so a term returning in a later section takes the parenthetical again.

**Never reword anything that has to be typed, opened, or clicked** — commands
(`poetry run garmin-coach report`), flags (`--confirm`), paths (`plans/2026-07-27_week.md`),
code references (`src/marts/features.py:120`). A reworded command cannot be pasted and a
reworded path is not clickable. They appear verbatim, with no parenthetical.

**No process jargon as a shortcut.** "Close the phase's user-facing DoD", "the
integrate-and-verify slice", "rollup within `features`" — say what actually happens instead.

## Asking a question (grilling, plan reviews, any mid-task decision)

Three failures, all taken from real questions asked in this repo:

**The choice is a data-structure name.** Unanswerable without reading the code.

> Bad: Wybierasz jednolity `end`-deskryptor, czy wolisz płaskie flagi?
>
> Good: Czy krok ma mieć jedno pole "co go kończy" (czas / dystans / tętno), czy trzy
> osobne, z których wypełniam jedno?

**The choice is a label.** `A` / `B` / `T3` force scrolling back to recover the meaning. The
label is a handle, never the content — restate the option in the question itself.

> Bad: Idziemy w A (rollup w ramach `features`), czy wolisz osobną komendę `weekly` (B)?
>
> Good: Czy tygodniówki mają się przeliczać same przy nocnym przeliczeniu, czy chcesz je
> odpalać osobno?

**"What changes" is missing.** State what each answer changes in something observable — a
number in the report, the pushed workout, a command to run, time spent. If nothing
observable changes, say that too; it tells the reader the question is about future
maintenance, not about their training.

Every question carries **a recommendation with its reason**. Not a menu — a recommendation
that can be overruled. This is already the good habit in this repo's history
(`**Moja rekomendacja: (b).** Powody: ...`); keep it.

## Writing a PRD or a ticket

The Problem Statement of issue #24 is the model to copy: it opens with what the athlete
cannot do, then a dated observation of it actually happening, then what went unrecorded. A
reader with no knowledge of the schema follows it end to end.

- **Lead with the capability gap**, in the athlete's terms — what they cannot do today, and
  what went wrong because of it.
- **Ground it in an observed event** with a date where one exists. A real Tuesday beats a
  hypothetical.
- **Scope bullets describe behaviour**, then name the fields. A bullet that is only field
  names is a schema diff, not a scope.
- **Name the consequence, not the value.** "the week was not marked as a deload in the plan"
  beats `was_deload: false` — a bare `false` adds nothing the sentence has not said.

## Give a number its direction when its scale is invented

Before quoting a number, ask: **can the reader judge for themselves whether it is good?**

- **Yes** — state it bare. `HR <= 154`, `5:32/km`, "12 tygodni do Hyrox". Glossing these
  patronises the reader.
- **No** — it sits on a methodology's invented scale (`strain`, `monotony`, `load_day`,
  `acwr`, `hrv_sd`, Training Effect). It arrives with a threshold, a comparison, or one word
  of assessment: "strain 778,3 (wysoki, ale przy tej monotonii normalny)".

Narrower than "always explain" on purpose — glossing a self-evident number is filler.

## Answer in the language of the question

Mirror the language the user wrote in, across **every part** of the answer: prose, headings,
list labels, severity words, table headers. A Polish question must not come back with English
fragments scattered through it (`warn`, `match: false`, `Signals`).

The boundary is scope, not topic:

| Follows the question's language | Always English |
| --- | --- |
| chat replies, grilling questions | code, comments, docstrings |
| `reports/` (the coach report) | log messages, error messages, `--help` text |
| `plans/`, `memory/` | `docs/` and the PRDs, `CLAUDE.md`, `AGENTS.md`, ADRs |
| | commit messages, PR descriptions, GitHub issues |

The right-hand column is not a matter of taste — it is what other agents, `git log`, and
future readers depend on. A request written in Polish still produces an English commit
message, an English ticket body, and an English PR description (`git-commits.md`).
