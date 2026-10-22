# ADR 0007 - Phase 6: personal training zones (`athlete_zones` mart)

## Status

Accepted

## Context

Intensity advice needs the athlete's recorded threshold anchor, not a fixed population HR ceiling.

## Decision

- `athlete_zones` is a recomputed singleton. Pure total zone computation reads core
  and policy; rollup runs within `features` and never contacts Garmin.
- Derive HR bands from recorded LTHR and configured fractions, leaving captured watch
  zone times unchanged. Z2 pace uses an aerobic-run pace/HR regression only with
  sufficient count and fit quality; exclude hot-weather runs. Otherwise use the
  recorded threshold pace times the configured multiplier. Missing anchors remain unknown.
- Source metadata names the pace-ceiling method plus LTHR provenance; it does not
  imply the recorded threshold pace itself was a fallback estimate.
- Fetch LTHR once per ETL run: ranged detection history for backfill, latest for sync.
  Pure normalizers emit only owned date/HR/pace columns and tolerate missing payloads;
  the library's merged latest response differs from its ranged parallel series.
- Weather is per-activity best-effort enrichment, filed under its activity date;
  convert Garmin Fahrenheit to Celsius. Failure leaves temperature unavailable.
- Anchor staleness and age are quiet metadata, not an alert. The digest also exposes
  personal-Z2 running-minute share beside the separate load-share comparison.
- ADR 0017 bounds all source reads and staleness to the selected horizon.

## Consequences

Regression replaces fallback as eligible evidence accumulates; no anchor or weak
fit never creates a fabricated measurement. Threshold coefficients live in policy,
not duplicate documentation tables. Real transport acceptance is separate from
normalizer and mart tests.
