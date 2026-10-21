# AI Usage

## 1. Tools Used

- **Claude (Anthropic):** Used through Claude Code with a sandboxed Linux environment to assist with project planning, implementation, testing, debugging, and documentation.
- **ChatGPT (OpenAI):** Used to clarify assignment requirements, discuss the proposed architecture and business rules, prepare the implementation prompt, review the project, and guide local setup and verification.

## 2. Approximate Contribution Split

The project was completed with AI assistance and developer direction and review. The estimated overall contribution split is:

- **AI-assisted work: 60%** — implementation assistance, test generation, debugging support, documentation drafting, and technical review.
- **Developer contribution: 40%** — interpreting the assignment and interviewer expectations, setting and confirming scope and business assumptions, directing implementation, reviewing design choices and results, checking expected financial calculations, running the project locally, executing tests and quality checks, manually exercising key API flows, and preparing the submission.

These percentages are approximate estimates of overall contribution, not a precise measurement of lines of code. They should not be interpreted as a claim that every line of code was written manually by the developer. The developer is responsible for understanding and verifying the submitted work.

## 3. What AI Assisted With

AI tools helped to:

- Translate the assignment brief into an implementation plan.
- Design the modular FastAPI application and SQLAlchemy models.
- Implement trade ingestion, database-backed idempotency, position updates, FIFO inventory matching, P&L calculations, and reconciliation.
- Develop unit, integration, and concurrency tests.
- Investigate test failures and improve validation and error handling.
- Draft and review `README.md`, `DESIGN.md`, and this `AI_USAGE.md`.
- Review design trade-offs and document limitations.

AI-generated code and documentation were treated as work products to inspect and verify, not as automatically correct output.

## 4. Developer Involvement and Business Assumptions

The developer provided the assignment and interviewer requirements, directed the solution toward a focused backend rather than an over-engineered distributed system, reviewed scope and design decisions, and performed local verification.

The implemented business assumptions include:

- Long-only inventory; short selling is out of scope.
- FIFO accounting for realized P&L and remaining inventory.
- Idempotency based on venue and trade ID, enforced by a database uniqueness constraint.
- Oversells are rejected without committing partial changes.
- Supplied mock mark prices are used instead of a live market-data feed.
- Reconciliation compares internal data with supplied external trade and position snapshots and is read-only.
- FIFO follows trade acceptance order; historical event-time reprocessing is not implemented.

## 5. Verification Performed

### Local verification on the developer's machine

The following checks were run locally on Windows using Python 3.14.4 and a locally installed PostgreSQL 16.15 instance:

- PostgreSQL service was running; the `trader` user and `trades` and `trades_test` databases were created.
- The Alembic migration `alembic upgrade head` completed successfully.
- The FastAPI application started with Uvicorn.
- `GET /health` returned `{"status":"ok"}`.
- `GET /health/ready` returned `{"status":"ok","database":"up"}`.
- Unit tests: **74 passed**.
- Full test suite, including PostgreSQL-backed integration and concurrency tests: **198 passed, 1 warning**.
- `ruff check .`: passed with “All checks passed!”
- `ruff format --check .`: passed with “41 files already formatted”.
- Manual API checks were performed through Swagger UI for duplicate-trade idempotency, conflicting duplicate handling, oversell prevention, and the FIFO P&L example.

The single test warning came from the FastAPI/Starlette test-client dependency stack; it did not cause the test suite to fail.

### Additional development-sandbox checks

The development work also reported these checks as executed in its sandbox:

- Alembic migration upgrade/downgrade/upgrade round trip and comparison of the migration schema with SQLAlchemy models.
- Exercising documented README flows against a running Uvicorn server using `curl`.
- A four-process Uvicorn test with 12 concurrent HTTP SELL requests against a position of 10 units: three requests were accepted, nine rejected, and the final position was one unit.
- Mutation checks for concurrency safeguards: removing the row lock caused 11 of 26 concurrency tests to fail; removing unique-constraint race handling caused five tests to fail. The original code was restored and re-verified.

These additional sandbox results are reported from the development environment and were not all independently repeated locally. They should not be described as personally executed locally unless independently repeated.

## 6. Financial and Business-Rule Validation

The FIFO example was checked against the assignment's expected scenario:

1. Buy 10 units at 100.
2. Buy 10 units at 120.
3. Sell 15 units at 150.

Expected results before fees:

- Realized P&L: 650.
- Remaining inventory: 5 units with a cost basis of 120 per unit.
- Unrealized P&L at a supplied mark price of 140: 100.
- Total P&L: 750.

The developer manually exercised the FIFO P&L example through the API and confirmed that the core calculation worked.

## 7. AI-Generated Issues Found During Development

The development process reported the following issues as found and corrected:

1. **Incorrect test expectations:** Two test expectations contained arithmetic errors (unrealized P&L expected as 120 instead of 280, and realized P&L expected as 10 instead of 30). The expectations were corrected after checking the arithmetic.
2. **Decimal precision in a test:** An extreme-precision test computed its expected value using Python's default Decimal context, which rounded the expected result. The test expectation was rewritten to construct the value exactly.
3. **Timestamp validation:** Pydantic initially accepted an integer Unix timestamp. The schema was tightened to require an ISO-8601 timestamp string with a UTC offset, and a test was added.
4. **Sandbox/tooling issues:** A package installation exceeded a tool time limit and was rerun; PostgreSQL stopped between steps and was restarted; and a process-termination pattern matched the shell running the command and was replaced with a safer approach. These were development-environment issues, not reported defects in the final service.

## 8. Limitations and Items Not Verified

- `docker-compose.yml` was **not executed locally**, because Docker was unavailable. Local database verification used PostgreSQL 16.15 installed directly on Windows.
- The local verification was performed with Python 3.14.4. Earlier development-sandbox verification was reported with Python 3.12.
- Dependencies are lower-bounded rather than fully pinned or locked.
- FIFO follows acceptance order, not event time; late-arriving historical trades do not trigger full inventory replay.
- Fees, short selling, authentication, derivatives contract multipliers, funding, and multi-currency P&L are outside the implemented scope.
- The project should not be described as pushed to GitHub until the repository has actually been pushed and checked.

## 9. Final Responsibility

AI tools assisted substantially with implementation and documentation, but they do not replace developer responsibility. The developer must ensure the final repository is accurate, inspect the submitted files, avoid committing secrets or unintended files, and be able to explain the idempotency, transaction, locking, FIFO, and reconciliation decisions in a follow-up interview.

This document should be updated if further code changes, testing, or review occur before submission.
