# ADR 0016 - Strength/HIIT authoring: flat steps and a taxonomy-held whitelist

## Status

Accepted

## Context

Strength/HIIT needs exercise metadata the typed running library cannot author.

## Decision

- Expand each set into a flat executable step; exercise repeat groups remain unproven.
  Drop trailing rest. Represent nonuniform sets as consecutive exercise entries.
- Use a curated exercise whitelist checked against a frozen public Garmin taxonomy;
  never derive enums from athlete history or guess missing values.
- Unknown names warn and retain athlete notes rather than blocking authoring. Ski erg
  remains unmapped in the Garmin enum map; burpee broad jumps use the honest BURPEE
  approximation. Movement mapping is a different vocabulary from Garmin workout enums.
- Weights are kilograms throughout request, spec and Garmin translation. Captured live
  payload evidence and per-sport weight-descriptor acceptance remain separately required.

## Consequences

Exercise authoring shares the pure spec/publisher boundary. Taxonomy contracts are
offline-testable; passing them does not prove native per-sport acceptance.
