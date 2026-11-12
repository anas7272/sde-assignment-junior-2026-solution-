# AI Usage

## Tools used and what they did
- **ChatGPT and Claude (not Claude Code or Codex):** Muhammad Anas used both to analyze the
  assignment, discuss implementation strategy and trade-offs, and compare the models' pros and
  cons. The candidate made the final design decisions.
- **Copilot SDK in VS Code (follow-up repository work):** reviewed the implementation against the
  specification, fixed the event duplicate/timestamp validation order and added regression tests,
  corrected setup/submission documentation and `.gitignore`, fixed the cross-check tool's
  workflow-specific reference handling, and ran tests and official-format integration validation.

## Where AI was wrong or unhelpful
An earlier draft incorrectly claimed Claude wrote the pipeline and supporting tools and said the
official schemas and generator were unavailable. The candidate clarified the actual AI-use
history, and this review confirmed the official files are present. The disclosure and submission
notes were corrected; the official generator was then run against the implementation.

The first attempt to use the SQL-free report checker against the official two-workflow data also
failed because it hard-coded `policy_number` as the external reference. That assumption was fixed
to use each workflow's schema reference field, and the checker then agreed on all five reports.

## Verification
- 26 unit and end-to-end tests passed, including duplicate IDs with invalid timestamps.
- Generated official-format small data (`--seed 2026`) ingested successfully; all five reports
  matched the SQL-free checker.
- Re-ingesting both drops skipped all eight input files and did not change report hashes.
- The private evaluator's expected outputs are not available, so no evaluator diff is claimed.
