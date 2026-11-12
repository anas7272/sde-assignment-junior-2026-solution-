"""Entry point: python main.py ingest data/drop_1 | report --out reports/"""
import sys
from ledger.cli import main

if __name__ == "__main__":
    sys.exit(main())
