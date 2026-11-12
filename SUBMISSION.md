# Submission: Assignment A — Interaction Ledger & Reporting Pipeline

## Candidate
- **Name:** Muhammad Anas.
- **Assignment chosen:** A (Data).
- **Time spent:** 3 hours.

## How to run
From a clean checkout and repository root:

```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python assignment-a-data/solution/main.py --db submission.db ingest data/drop_1 --schemas assignment-a-data/schemas
python assignment-a-data/solution/main.py --db submission.db ingest data/drop_2 --schemas assignment-a-data/schemas
python assignment-a-data/solution/main.py --db submission.db report --out reports
python -m unittest discover -s assignment-a-data/solution/tests -t assignment-a-data/solution -v
```

Assignment A uses only Python 3.11+ standard-library modules; no dependency installation is
required. Use a fresh database for different input data. Detailed design, assumptions, and
validation are in [assignment-a-data/solution/SUBMISSION.md](./assignment-a-data/solution/SUBMISSION.md).

## Design and failure handling
SQLite stores schemas/reference data, leads, normalized lead fields, rejections, file hashes and
counters, and an append-only event ledger. The unique workflow + keyed phone hash identifies a
lead without storing the phone in plaintext. Schema-marked PII fields are encrypted. Event-to-lead
links are derived in a view so late leads resolve earlier orphans without mutating events.

Input files are processed transactionally. Re-ingesting unchanged files is a no-op; changed
content at a previously ingested path is refused. Rejections store no source values. Reports are
recomputed from full ledger state rather than incrementally maintained. The stdlib-only
encryption implementation is a take-home trade-off; production should use reviewed AEAD and
KMS-managed keys.

## Testing and results
The automated tests cover normalization, merge/default behavior, PII-at-rest checks, append-only
events, overrides, buckets, late leads, event duplicate/timestamp validation ordering, idempotency,
and report outputs. Generated-data integration passed on the official generator
(`--seed 2026 --scale small`): 2,070 non-blank lead rows and 9,027 event lines; all five reports
matched the SQL-free oracle. Re-ingesting both drops skipped all eight files and left report hashes
unchanged. The test suite passed all 26 tests. The
evaluator's private expected outputs are unavailable; no evaluator diff is claimed.

## Candidate actions before sending
Ensure the private repository is shared with the required reviewers and complete the required
submission email.
