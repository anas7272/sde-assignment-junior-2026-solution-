# Interaction Ledger & Reporting Pipeline

Pure Python 3.11+ standard library (SQLite). No dependencies to install.
For Assignment A, you do not need to install the repository's `requirements.txt`; it contains
dependencies for the separate Assignment B implementation.

```bash
# from the repository root
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
python assignment-a-data/solution/main.py ingest data/drop_1 --schemas assignment-a-data/schemas
python assignment-a-data/solution/main.py ingest data/drop_2 --schemas assignment-a-data/schemas
python assignment-a-data/solution/main.py report --out reports
```

The commands above are for a clean checkout. Use a fresh database for a different dataset;
otherwise the file identity and SHA-256 checks intentionally reject changed input files.
Options (before the sub-command): `--db PATH` (default `$LEDGER_DB` or `./ledger.db`).
`ingest` takes `--schemas DIR` (or `$LEDGER_SCHEMAS`).

PII key: `$LEDGER_PII_KEY`, otherwise a private key file `<db>.key` is created next to the DB.
`*.key` is git-ignored. Back up the key separately from the database; without it phone matching
and PII decryption are impossible.

```bash
# from the repository root
python -m unittest discover -s assignment-a-data/solution/tests -t assignment-a-data/solution -v
python assignment-a-data/solution/tools/stress_gen.py --out C:\Temp\ledger-stress
python assignment-a-data/solution/main.py --db C:\Temp\ledger-stress.db ingest C:\Temp\ledger-stress\data\drop_1 --schemas C:\Temp\ledger-stress\schemas
python assignment-a-data/solution/main.py --db C:\Temp\ledger-stress.db ingest C:\Temp\ledger-stress\data\drop_2 --schemas C:\Temp\ledger-stress\schemas
python assignment-a-data/solution/main.py --db C:\Temp\ledger-stress.db report --out C:\Temp\ledger-stress\reports
python assignment-a-data/solution/tools/oracle_check.py C:\Temp\ledger-stress C:\Temp\ledger-stress\schemas C:\Temp\ledger-stress\reports
```

The stress generator is independent synthetic data for performance checks; it is not the
assignment's official generator or an evaluator oracle. The official generator and schemas are
the source used for the integration run described in `SUBMISSION.md`.

Layout: `ledger/normalize.py` (phones, dates, decimals, timestamps) · `schema.py` · `pii.py` ·
`db.py` (DDL, triggers, link views) · `ingest.py` · `derive.py` (effective disposition, buckets) ·
`report.py` · `cli.py`.
