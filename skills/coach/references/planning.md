# Planning the week

Read this before proposing or writing a training week. The signal codes referenced below
are mapped to their meaning in `references/report.md` ("Sygnały").

The athlete's plan of record is `plans/<monday>_week.md`, one file per week, authored by
hand and revised mid-week when signals warrant. It drives today's planned intent, the
recommendation's starting point, and weekly adherence. A week with no file falls back to
the repeating `plan_template` - a default shape, never an agreed plan.

**Read it** with `mcp__coach__get_plan(week_start)` when the tools are present (defaults
to the current week). `has_plan: false`, every day sourced `plan_template`, or a
`PLAN_MISSING` signal all mean the same thing: that week is unplanned. Say so plainly.

**Propose one** when the athlete asks, or when you spot an unplanned week - offer, never
write unasked. Compose the seven days yourself from what you already read (`get_weekly`
for the recent shape and adherence, `get_digest` for form and signals, `get_events` for
what they are training for); the deterministic layer only validates and stores. Each day
is `{planned, intent}`:

- **`planned`** - the free-text session, in the athlete's own style ("bieg easy 10 km,
  Zone 2 (HR <145)"). It carries the detail the engine cannot hold: paces, HR caps,
  distances. It cannot contain `|` or a line break - both break the plan file's table
  row, and the tool will reject the proposal rather than corrupt it. Rephrase instead.
- **`intent`** - exactly one of `rest | easy | tempo | strength | hyrox | crossfit |
  quality`. This is what the engine reads: it names how hard the day is, not the
  exercises. `crossfit` and `hyrox` both mean a hard mixed session; the discipline lives
  in `planned`.

Then `plan_preview(week_start, days)` to validate, **show the athlete the table**, and
only on their explicit go-ahead `plan_confirm(week_start, days)`, which writes the file
and caches it. Confirm refuses a week that already has a file - revisions are the
athlete's own edit plus `garmin-coach plan import`, so their prose and revision log are
never overwritten by a tool that cannot read them. Without the MCP tools, write the file
in the same table format by hand and run `plan import`.
