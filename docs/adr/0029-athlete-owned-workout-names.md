# ADR 0029 - The workout's name is the athlete's, and a date-free name is reusable

## Status

Accepted

## Context

Athletes need watch-readable names and reusable workouts without destructive replacement of other scheduled days.

## Decision

- Label and full name are mutually exclusive; trim and refuse empty/control/multiline
  or overlong input and GC-prefixed labels. Name participates in account fingerprint.
- Coach ownership comes from the gc-hash tag, not a name prefix. Date-free naming
  enables reuse; copying a spec to another date reruns date and plan guards.
- Replace deletes the old library workout only if its account name carries the
  requested date. Otherwise unschedule that day, keep the reusable library entry and
  upload the replacement. Protect unknown manually scheduled days.
- Preview names the action and warns of same-name ambiguity; never silently tidy old
  library versions. Hash/name ambiguity refuses instead of guessing.
- The original lack of conversational unscheduling was superseded by ADR 0030.

## Consequences

Renaming changes identity deliberately. Reuse shares account content across dates;
replacement never assumes a local receipt knows every scheduled occurrence.
