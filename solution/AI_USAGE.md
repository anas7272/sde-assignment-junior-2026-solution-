# AI Usage Disclosure

## Tools used

- **ChatGPT:** Used to understand assignment requirements, discuss implementation strategies, troubleshoot issues, and review technical decisions.
- **Claude:** Used to discuss the assignment, explore implementation approaches, and compare possible design trade-offs.
- **GitHub Copilot SDK (VS Code):** Used during follow-up repository review and validation work.

## What I used them for

- Discussing the assignment specification and breaking down the required functionality.
- Exploring implementation approaches, technical trade-offs, and edge cases.
- Reviewing the implementation against the assignment requirements.
- Identifying and correcting the event duplicate/timestamp validation order and adding regression tests.
- Improving setup instructions, submission documentation, and `.gitignore`.
- Correcting the cross-check tool's workflow-specific reference-field handling.
- Running tests and validating the implementation against official-format generated data.

## What I wrote or decided myself

- I made the final design and implementation decisions.
- I evaluated the suggested approaches and decided which changes were appropriate for the assignment.
- I reviewed the AI-assisted changes and took responsibility for the final solution and submission.
- I used the assignment requirements and validation results to determine whether the implementation was ready.

## Where the AI was wrong or unhelpful

- An earlier AI-generated draft incorrectly described Claude as having written the pipeline and supporting tools. It also incorrectly stated that the official schemas and data generator were unavailable. I corrected the disclosure after checking the actual repository contents and AI-use history.
- The first version of the SQL-free report checker incorrectly assumed that `policy_number` was the external reference field for every workflow. This caused the initial comparison to fail. The assumption was corrected to use the reference field specified by each workflow's schema.

## How I verified AI-generated code

- Ran the unit and end-to-end test suite: **26 tests passed**.
- Generated official-format test data using the provided generator with `--seed 2026`.
- Ingested the generated data and compared all five reports against the independent SQL-free checker. The checker agreed on all five reports.
- Re-ingested both data drops to verify that already-ingested files were skipped.
- Compared report hashes before and after re-ingestion to verify that the reports remained unchanged.
- The private evaluator's expected outputs were not available, so I have not claimed verification against its results.
