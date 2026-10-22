# ADR 0002 - Phase 2 metrics (mart) semantics

## Status

Accepted

## Context

Derived metrics must reproduce the frozen reference analysis without mixing observations and estimates.

## Decision

- `features(conn)` reads core and upserts one daily mart row per calendar day from
  `data_start` through the selected horizon. Rest loads/zones are zero; unavailable
  recovery observations are null. Trailing reads extend beyond a narrowed `--from`.
- HRV baseline is the median of non-null nights in the computed window, capped to
  trailing 60 nights; SD uses `ddof=1`. The same current band is stamped on each row.
  The mart's low-HRV flag uses strict `< baseline - 1 * SD`, not a rounded
  comparison; the digest's configurable `hrv_low_k_sd` is separate signal policy.
- Acute load is trailing seven-day load / 7; chronic load is trailing 28-day load / 28.
  Fixed denominators include zero contributions before recorded history, so ACWR is
  indicative while `n_chronic` is below its configured reliability count. Earlier
  onboarding days remain evidence gaps, not observed rest.
- Bucket activities by local start date. Wellness RHR precedes sleep resting HR.
  Zone minutes are captured seconds / 60. Missing values remain total over nulls.
- Original whole-session TE buckets were superseded by ADR 0010's strength blend
  and ADR 0027's cardio split; the four buckets sum to daily blended load.
- Recompute by date upsert; marts are disposable, core is the system of record.

## Consequences

Historical HRV flags can shift as the current band changes. Frozen anonymized SQL
and the golden regression preserve independent numerical evidence, including the
June 17 rounding difference documented in the test; never regenerate from personal
storage as part of development testing.
