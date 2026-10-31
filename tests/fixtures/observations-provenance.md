# Observation fixture provenance

Anonymized response-only fixtures for issue #106. The weight recordings below
are upstream developer evidence; selected-account source checks are recorded separately.

Source: public developer VCR cassettes in matin/garth, commit
`f99159a15c4c9463ce215a60ba9f7cb21f94a3b7`.

| Extract | Original cassette |
| --- | --- |
| `weight_daily_single.json` | [test_get_daily_weight_data.yaml](https://github.com/matin/garth/blob/f99159a15c4c9463ce215a60ba9f7cb21f94a3b7/tests/data/cassettes/test_get_daily_weight_data.yaml) |
| `weight_daily_multiple.json` | [test_get_manual_weight_data.yaml](https://github.com/matin/garth/blob/f99159a15c4c9463ce215a60ba9f7cb21f94a3b7/tests/data/cassettes/test_get_manual_weight_data.yaml) |
| `weight_daily_empty.json` | [test_get_nonexistent_weight_data.yaml](https://github.com/matin/garth/blob/f99159a15c4c9463ce215a60ba9f7cb21f94a3b7/tests/data/cassettes/test_get_nonexistent_weight_data.yaml) |

## Extraction and anonymization

- Extracted only the decoded JSON response body of the one successful HTTP 200 GET to the weight service in each cassette.
- No HTTP headers, credentials, cookies, tokens, request bodies or other interactions were copied into these artifacts.
- Replaced every integer `samplePk` consistently across all files with deterministic fake integers beginning at 910001. The mapping from original IDs is not retained.
- Account, profile, owner, device and personal-name identifier fields were recursively filtered. The selected response bodies contain no such fields; no additional removal was needed.
- Original response envelopes, field names, scalar types, nulls, dates, timestamps, source types and measurement values/units were retained. Only measurement sample identifiers were changed.
- The timestamp difference between local `date` and UTC `timestampGMT` is preserved. `calendarDate` remains the source day; date boundaries were not inferred from UTC.
- These public developer fixture dates are not observations from this athlete. No real athlete installation, account, saved login or private files were accessed.

## Observed contracts

- Daily envelope: `startDate`, `endDate`, `dateWeightList`, `totalAverage`.
- Individual observations contain `samplePk`, `calendarDate`, local epoch-millisecond `date`, UTC epoch-millisecond `timestampGMT`, gram-scale `weight`, `sourceType`, and nullable body composition figures.
- The multiple-observation day contains MANUAL and INDEX_SCALE observations. A manual reading has nullable composition fields.
- Successful empty data is an empty observation list, not a zero weight.

Related primary parser/test references:
- [WeightData parser](https://github.com/matin/garth/blob/f99159a15c4c9463ce215a60ba9f7cb21f94a3b7/src/garth/data/weight.py)
- [Recorded behavior and timestamp tests](https://github.com/matin/garth/blob/f99159a15c4c9463ce215a60ba9f7cb21f94a3b7/tests/data/test_weight_data.py)

These cassettes establish the selected daily examples. They do not establish a history retention limit, maximum range length, pagination contract or historical edit/deletion semantics.

## Garmin race forecasts

`race_latest.json`, `race_daily_recent.json` and `race_daily_data_start.json` are
read-only developer captures from garminconnect 0.3.6 on 2026-10-31. Latest is an
object; daily history is a list. The history requests were 2026-10-28..30 and
2026-06-08..08. Account/profile/device/owner identifiers were removed recursively;
response envelopes, dates, numeric seconds and nulls were retained. Three
post-login forecast GETs were made using a private temporary copy of saved login.
The athlete's database was never opened, and no account observations were written.
These captures establish those examples, not universal history retention or an
empty/error response contract. Any deliberately altered error, partial or empty
test input is synthetic and is labelled in the test.

A separate selected-account check on 2026-10-31 made one GET through the new
weight transport for 2026-10-30. Its envelope matched the recorded daily contract
and passed validation in an isolated in-memory database. The source check used
only a temporary copy of saved login; no athlete database was opened or modified.
That response is not needed in the fixture corpus because the upstream examples
already cover the daily shapes. Both source checks are separate from offline tests.
