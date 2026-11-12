# Submission

## Candidate

- **Name:** Anas Muhammad
- **Assignment chosen:** A 
- **Time spent :** 4 hours

## How to run

Requirements: Python 3.11 or later. Assignment A uses only the Python standard library and SQLite. No third-party dependencies are required.

Run all commands from the repository root.

**1. Generate the official input data**

```bash
python assignment-a-data/tools/generate_data.py --seed 2026 --scale small --out data
```

**2. Ingest the first data drop**

```bash
python solution/main.py ingest data/drop_1 --schemas assignment-a-data/schemas
```

**3. Ingest the second data drop**

```bash
python solution/main.py ingest data/drop_2 --schemas assignment-a-data/schemas
```

**4. Generate reports**

```bash
python solution/main.py report --out reports
```

**5. Run the test suite**

```bash
python -m unittest discover -s solution/tests -t solution -v
```

A fresh database is used by default (`./ledger.db`). To use a different database, pass `--db PATH` before the subcommand.

The PII encryption key can be provided through `LEDGER_PII_KEY`. Otherwise, a separate key file is created next to the database. The database and key must be backed up together.

For a different dataset, use a fresh database because the ingestion process tracks file identity and SHA-256 hashes to prevent unintended reprocessing of changed files.

## Design

The solution is implemented in Python using SQLite as its persistent storage layer.

The implementation is divided into separate modules so that ingestion, normalization, database operations, PII handling, business-rule derivation, and reporting remain independent.

**Processing flow:**

```text
Input CSV / Event Files
          |
          v
     Schema Loading
          |
          v
   Data Normalization
          |
          v
    Validation
          |
          v
   SQLite Database
          |
          v
  Derived Business Rules
          |
          v
     Report Generation
          |
          v
       CSV Reports
```

**Main components:**

- `normalize.py`: Normalizes phone numbers, dates, decimals, and timestamps.
- `schema.py`: Loads and applies workflow-specific schema definitions.
- `pii.py`: Handles PII protection and phone-number matching.
- `db.py`: Manages SQLite database structures, triggers, and link views.
- `ingest.py`: Processes input files, validates records, and handles ingestion.
- `derive.py`: Calculates derived values such as effective dispositions and reporting buckets.
- `report.py`: Generates the required reports.
- `cli.py`: Provides the command-line interface.

**Database design:**

SQLite provides persistent storage and transactional operations without requiring an external database server.

The database separates the ingestion and processing responsibilities, maintains relationships between records, and uses database-level constraints and triggers where appropriate.

File identity and SHA-256 hashes are maintained to support safe re-runs and detect changed input files. Schema-specific reference fields are used instead of assuming that every workflow uses the same external identifier.

PII handling is separated from the main ingestion and reporting logic so that sensitive values can be protected while still supporting matching operations.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| SQLite for persistence | Keeps the solution self-contained and uses transactional database operations | PostgreSQL or an external database server |
| Python standard library | Avoids unnecessary dependencies for Assignment A | Third-party data-processing frameworks |
| Schema-driven ingestion | Different workflows can have different field names and reference fields | Hard-coded workflow-specific parsing |
| File identity and SHA-256 tracking | Prevents accidental duplicate ingestion and detects changed input files | Reprocessing every file on every run |
| Separate normalization and validation modules | Makes data-cleaning rules easier to maintain and test | Combining all logic inside ingestion |
| Separate PII handling | Keeps sensitive-data operations isolated | Storing and processing raw PII throughout the pipeline |
| Derived reporting values | Keeps business-rule calculations separate from raw ingestion | Calculating everything directly inside report generation |

## Failure modes considered

- **Invalid input records:** Validation and normalization handle malformed values and reject records that cannot be processed.
- **Duplicate input files:** File identity and SHA-256 tracking prevent unintended repeated ingestion.
- **Duplicate records and events:** Duplicate handling is implemented to avoid repeatedly processing the same information.
- **Invalid timestamps:** Timestamp validation is handled before duplicate-event decisions, with regression coverage for duplicate IDs containing invalid timestamps.
- **Unknown dispositions:** These are handled separately from recognized dispositions during processing and reporting.
- **Orphan events:** Events that cannot be linked to a corresponding lead are accounted for rather than silently treated as successfully linked.
- **Re-running ingestion:** Previously ingested files are skipped.
- **Changed input files:** Changed content is detected using SHA-256 checks. A fresh database is required for a different dataset.
- **PII key loss:** Without the corresponding key, protected PII cannot be decrypted and phone-number matching cannot be performed.
- **Database and process failures:** SQLite transactions provide transactional protection for database operations.

**Known limitations:**

- The private evaluator's expected outputs were not available, so comparison against its final expected results has not been performed.
- The custom PII protection implementation has not undergone an independent cryptographic security audit.
- Arbitrary PII embedded in unexpected event-payload fields is not guaranteed to be detected and protected.
- Large-scale production performance and recovery under every possible crash scenario have not been fully validated.
- Windows file permissions for the encryption key have not been independently verified.

## Testing

**What I tested:**

- Unit and end-to-end tests covering normalization, validation, ingestion, database operations, and reporting.
- Duplicate event IDs with invalid timestamps to verify validation ordering.
- Ingestion using official-format generated data with seed `2026`.
- Both official data drops.
- All five generated reports against the independent SQL-free report checker.
- Re-ingestion of previously processed files.
- Report SHA-256 hashes before and after re-ingestion to confirm that the reports remained unchanged.

**Test results:**

- 26 unit and end-to-end tests passed.
- Official-format integration run completed successfully.
- The independent checker agreed on all five reports.
- Re-ingestion skipped all eight input files.
- Report hashes remained unchanged after re-ingestion.

**What I did not test:**

- Comparison against private evaluator expected outputs, because they were unavailable.
- Full production-scale performance under very large datasets.
- Every possible process-crash and operating-system failure scenario.
- An independent security audit of the PII protection implementation.

## Results

The implementation successfully processed both official-format data drops generated using seed `2026`.

The ingestion and reporting pipeline produced all five reports, and the independent SQL-free checker agreed with their results.

Re-running ingestion demonstrated idempotent behavior: all eight previously processed input files were skipped, and the generated report hashes remained unchanged.

The integration run also exercised multiple data-quality cases, including duplicate records, malformed events, orphan events, and unknown dispositions.

Exact execution timings were not recorded, so no performance timing claims are made.

## If I had another day

1. Perform larger-scale performance and memory testing and optimize ingestion where necessary.
2. Expand crash-recovery and transaction-rollback testing.
3. Strengthen PII protection, including reviewing unexpected payload fields and performing an independent security review.
4. Add more edge-case tests for malformed input, unusual timestamps, and schema variations.
5. Improve operational observability with structured logs and clearer ingestion summaries.
6. Validate the final output against the evaluator's expected results if those become available.
