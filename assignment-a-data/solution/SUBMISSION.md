# SUBMISSION: Assignment A

## Candidate
- **Name:** Muhammad Anas.
- **Assignment chosen:** A (Data).
- **Time spent:** 5-6 hours.

## How to run
From a clean checkout, follow the commands in [README.md](./README.md) from the repository root.
They generate fresh official-format input, ingest both drops, write all five reports, and run the
test suite. Use a fresh `--db PATH` for each different input dataset.

## Architecture
```text
CSV batch ─► header map ─► validate/normalise ─► leads + encrypted fields / rejections
JSONL     ─► parse ─► dedupe ─► append-only events (phone as keyed hash)
report    ─► derived event links ─► calls ─► buckets, daily overview, lead report
```

SQLite stores schemas/reference data, leads, normalized fields, rejections, file hashes/counters,
and events. Each input file is one transaction. `UNIQUE(workflow_id, phone_hash)` defines lead
identity. `UNIQUE(event_id)` deduplicates events, and triggers reject event updates/deletes.
Event-to-lead links are derived in a view rather than stored, so late leads resolve earlier
orphans without changing the ledger.

## PII
Phone values are matched through HMAC-SHA256 with a secret key; only the last four digits are
stored in clear for the masked report. Other schema-marked PII fields are encrypted, and event
records do not retain the top-level phone. Rejections store no source values. The current
stdlib-only authenticated-encryption construction is a take-home trade-off, not recommended for
production; production should use a reviewed AEAD library with KMS-managed keys and key rotation.

## Idempotency and failure modes
Re-ingesting unchanged files is a no-op. A previously ingested path with changed content is
refused rather than double-counted. Per-file transactions prevent partial file ingestion. Reports
are recomputed from all ledger state, so late leads can resolve orphans and incremental ingestion
matches a single full run. Report generation is not incremental and scales with the full history.

## Decisions and assumptions
- A required non-phone field with an invalid value is rejected as `missing_required:<field>`; an
  optional invalid value becomes null and increments warnings.
- An optional phone field with invalid content is a warning; the identity phone rejects as
  `invalid_phone`.
- Event JSON and missing `event_id` are checked first, then duplicate IDs, then timestamps.
  An invalid timestamp on a new event is malformed and does not reserve its ID; an already-seen
  ID is counted as a duplicate even if that line has an invalid timestamp.
- Unknown dispositions are evaluated after final orphan linking; orphan disposition events count
  only as orphans.
- If multiple CSV columns map to one field, the first matching column wins.
- `rejections_by_reason` omits zero-count reasons; batches with no accepted leads are omitted from
  `batch_summary.csv`.
- Equal timestamps use ledger arrival order as a tie-breaker. Decimal rounding is half-up.
- Calls are keyed by workflow and call ID; only events linked to the `call.initiated` lead
  contribute to that call's facts.

## Testing and results
The automated suite uses hand-built fixtures with expected output derived from the spec and tests
normalization, merging, PII-at-rest checks, append-only behavior, event overrides, buckets,
late-arriving leads, duplicate/timestamp validation ordering, idempotency, and report files.
**26 tests passed** with
`python -m unittest discover -s assignment-a-data/solution/tests -t assignment-a-data/solution -v`.

The official generator (`--seed 2026 --scale small`) ran against the repository schemas. It
produced 2,070 non-blank lead rows and 9,027 event lines. Both drops ingested, all five report
files were written, and the generalized SQL-free oracle agreed on all five reports. Ingestion
took about 0.47 seconds combined and report generation about 0.10 seconds in this environment.
Re-ingesting both drops skipped all eight files, and SHA-256 checks confirmed the reports did not
change. These are small-scale integration results, not a large-scale performance claim.
The private evaluator's expected outputs are not available, so no evaluator diff is claimed.

The separate `tools/stress_gen.py` creates custom, approximately large-scale data. Its
`tools/oracle_check.py` can cross-check that fixture too, but this custom stress dataset is not
the official `--scale large` data or an evaluator oracle.

## Production note and next steps
For 100k leads/day, schedule ingestion per delivered file with hash-based retries, alert on failed
ingests and data-quality changes, and backfill into a scratch database before report comparison.
Priorities are production-grade AEAD/KMS, incremental rollups, SQL aggregation, PostgreSQL for
concurrent writers, and alerting/replay for long-lived orphans.
