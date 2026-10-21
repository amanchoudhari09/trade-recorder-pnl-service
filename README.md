# Trade Recorder & P&L Service

A backend service that ingests trades from multiple venues, maintains
long-only positions, calculates FIFO realized and unrealized profit and
loss (P&L), and compares the internal ledger with caller-supplied venue
snapshots.

The service is built with **FastAPI, SQLAlchemy, and PostgreSQL**. It
includes database-enforced idempotency, oversell prevention,
transactional trade processing, and read-only reconciliation.

> This is a take-home assignment and a focused demonstration of backend
> engineering and accounting logic---not a production-ready trading
> system. See [Known limitations](#known-limitations).

-   Design decisions, schema, and concurrency strategy:
    [DESIGN.md](DESIGN.md)
-   AI tools and assistance disclosure: [AI_USAGE.md](AI_USAGE.md)

## Table of contents

-   [Features](#features)
-   [Scope and assumptions](#scope-and-assumptions)
-   [Tech stack](#tech-stack)
-   [Architecture](#architecture)
-   [Prerequisites](#prerequisites)
-   [Local setup](#local-setup)
-   [Database migrations](#database-migrations)
-   [Run the service](#run-the-service)
-   [API reference](#api-reference)
-   [FIFO P&L example](#fifo-pnl-example)
-   [Idempotency and oversell
    behavior](#idempotency-and-oversell-behavior)
-   [Reconciliation](#reconciliation)
-   [Tests and code quality](#tests-and-code-quality)
-   [Known limitations](#known-limitations)

## Features

-   **Trade ingestion:** accept caller-supplied trades through an HTTP
    API.
-   **Idempotency:** identify trades by `(venue, trade_id)` using a
    database uniqueness constraint.
-   **FIFO accounting:** match SELL trades against the oldest open BUY
    lots in acceptance order.
-   **Long-only inventory:** reject SELLs that exceed the available
    position.
-   **Transactional processing:** apply trade, lot, position, and P&L
    changes atomically.
-   **Concurrency protection:** serialize competing updates to the same
    position using PostgreSQL row locking.
-   **P&L reporting:** report realized, unrealized, and total P&L where
    a mark price is available.
-   **Mock mark prices:** set mark prices explicitly through an API
    endpoint.
-   **Reconciliation:** compare internal trades or positions with
    snapshots supplied by the caller, without modifying the ledger.
-   **Operational endpoints:** separate liveness and database-readiness
    checks.

## Scope and assumptions

  -----------------------------------------------------------------------
  Topic                               Decision
  ----------------------------------- -----------------------------------
  Venues                              Any non-empty venue identifier;
                                      trades are supplied by the caller.
                                      No exchange integration is
                                      included.

  Trade identity                      `(venue, trade_id)`. Venue
                                      identifiers are normalized to
                                      lowercase.

  Position key                        `(account_id, venue, symbol)`.
                                      Positions are not netted across
                                      accounts, venues, or symbols.

  Position policy                     Long-only. A SELL larger than
                                      available inventory is rejected
                                      with HTTP `422`. Short selling is
                                      not supported.

  Accounting                          FIFO. SELLs consume the oldest BUY
                                      lots first, in **trade acceptance
                                      order**, not event-timestamp order.

  Precision                           Monetary and quantity values use
                                      `Decimal`; decimal values in JSON
                                      requests should be sent as strings,
                                      such as `"100.50"`.

  Currency                            P&L is expressed in the
                                      instrument's quote currency.
                                      Aggregating P&L across symbols
                                      assumes compatible quote
                                      currencies.

  Mark price                          Supplied manually through
                                      `PUT /market-prices/{symbol}`. If a
                                      mark price is missing, unrealized
                                      P&L and total P&L are `null` where
                                      applicable; missing marks are not
                                      treated as zero or replaced with
                                      the last trade price.

  Fees                                Not included. Reported P&L is
                                      before fees, funding, and rebates.

  Timestamps                          ISO 8601 timestamps with a UTC
                                      offset are required, for example
                                      `2026-01-01T12:00:00Z`. Naive
                                      timestamps are rejected.

  Default account                     `account_id` defaults to
                                      `"default"` when omitted.

  Authentication                      Not implemented; authentication and
                                      authorization are outside this
                                      assignment's scope.
  -----------------------------------------------------------------------

## Tech stack

-   Python 3.11+
-   FastAPI
-   Pydantic v2
-   SQLAlchemy 2.x (synchronous sessions)
-   PostgreSQL 16
-   Psycopg 3
-   Alembic
-   Pytest and HTTPX
-   Ruff
-   Docker Compose configuration for PostgreSQL

## Architecture

``` text
app/
  main.py                 FastAPI application and router registration
  config.py               Environment-based settings
  database.py             SQLAlchemy engine and request-scoped sessions
  models.py               SQLAlchemy models
  schemas.py              Pydantic request/response models and validation
  errors.py               Domain exceptions
  money.py                Decimal context and formatting helpers
  api/
    deps.py               API dependencies
    handlers.py           Error handlers
    health.py             Liveness and readiness endpoints
    trades.py             Trade endpoints
    portfolio.py          Positions, P&L, and mark-price endpoints
    reconciliation.py     Reconciliation endpoints
  services/
    fifo.py               FIFO matching and P&L calculations
    ingestion.py          Idempotency, locking, and transactional application
    portfolio.py          Position, P&L, and mark-price operations
    trades.py             Trade queries
    reconciliation.py     Read-only snapshot comparisons
    comparison.py         Shared trade-comparison logic
migrations/               Alembic migrations
tests/
  unit/                   Unit tests that do not require a database
  integration/            PostgreSQL-backed API, constraint, and concurrency tests
```

The database schema includes trade records, positions, open lots, lot
matches, and market prices.

## Prerequisites

-   Python 3.11 or newer
-   PostgreSQL 16, either installed locally or run with Docker Compose
-   Git (optional, for cloning the repository)

## Local setup

Run these commands from the project root.

### 1. Create and activate a virtual environment

**Windows PowerShell:**

``` powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

``` bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

``` bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 3. Configure environment variables

Copy the example configuration:

**Windows PowerShell:**

``` powershell
Copy-Item .env.example .env
```

**Linux/macOS:**

``` bash
cp .env.example .env
```

The example values are intended for local development. Change them if
your PostgreSQL setup uses different credentials or database names.
Never commit real secrets or a real `.env` file.

### 4. Start PostgreSQL

**Option A --- Docker Compose**

``` bash
docker compose up -d db
```

The Compose configuration starts PostgreSQL on `localhost:5432`. The
configured application database is `trades`. The `trades_test` database
is initialized on the first start of a fresh database volume. If the
volume already existed before the test database was configured, create
it once:

``` bash
docker compose exec db createdb -U trader trades_test
```

The Docker Compose setup is provided for convenience. It was not
executed in the author's local verification environment; local
verification used an installed PostgreSQL 16 instance.

**Option B --- Existing PostgreSQL**

Create the application and test databases, and configure the connection
strings in `.env`. The example connection strings are:

``` dotenv
DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trades
TEST_DATABASE_URL=postgresql+psycopg://trader:trader@localhost:5432/trades_test
```

Use credentials appropriate for your own local database. The test
database name must end in `_test`: integration tests truncate tables and
are designed to refuse to run against a database whose name does not end
in `_test`.

## Database migrations

Apply the schema to the database configured by `DATABASE_URL`:

``` bash
alembic upgrade head
```

## Run the service

``` bash
uvicorn app.main:app --reload
```

The service will normally be available at `http://localhost:8000`.

-   Interactive API documentation: <http://localhost:8000/docs>
-   OpenAPI schema: <http://localhost:8000/openapi.json>
-   Liveness check: <http://localhost:8000/health>
-   Readiness check: <http://localhost:8000/health/ready>

`/health` checks that the application process responds. `/health/ready`
also checks database connectivity.

## API reference

  ------------------------------------------------------------------------------
  Method                  Endpoint                       Purpose
  ----------------------- ------------------------------ -----------------------
  `GET`                   `/health`                      Liveness check; does
                                                         not access the database

  `GET`                   `/health/ready`                Readiness check;
                                                         executes a database
                                                         connectivity check

  `POST`                  `/trades`                      Record a trade; `201`
                                                         for a new trade, `200`
                                                         for an identical
                                                         duplicate, `409` for a
                                                         conflicting duplicate,
                                                         `422` for invalid input
                                                         or oversell

  `GET`                   `/trades`                      List trades; supports
                                                         `venue`, `symbol`,
                                                         `account_id`, `limit`
                                                         (1--500, default 50),
                                                         and `offset` filters

  `GET`                   `/trades/{venue}/{trade_id}`   Fetch a trade by venue
                                                         identity; `404` if it
                                                         does not exist

  `GET`                   `/positions`                   Return open positions,
                                                         FIFO lots, average
                                                         cost, and P&L; supports
                                                         `account_id`, `venue`,
                                                         and `symbol` filters

  `GET`                   `/pnl`                         Return aggregate P&L
                                                         for the selected
                                                         filters

  `PUT`                   `/market-prices/{symbol}`      Set the mock mark price
                                                         for a symbol

  `POST`                  `/reconciliation/trades`       Compare a supplied
                                                         trade snapshot with the
                                                         internal ledger;
                                                         read-only

  `POST`                  `/reconciliation/positions`    Compare supplied
                                                         expected quantities
                                                         with internal
                                                         positions; read-only
  ------------------------------------------------------------------------------

Errors use a consistent response structure:

``` json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable explanation",
    "details": {}
  }
}
```

Common error codes include `validation_error` (`422`), `trade_conflict`
(`409`), `insufficient_inventory` (`422`), `not_found` (`404`),
`database_unavailable` (`503`), and `internal_error` (`500`).

## FIFO P&L example

This example demonstrates FIFO matching for `BTC-USD`. It assumes the
service is running locally and the API is reachable at
`http://localhost:8000`.

Submit two BUY trades and one SELL trade:

``` bash
# BUY 10 @ 100
curl -s -X POST http://localhost:8000/trades \
  -H 'Content-Type: application/json' \
  -d '{
    "venue": "binance",
    "trade_id": "B-1",
    "account_id": "desk-1",
    "symbol": "BTC-USD",
    "side": "BUY",
    "quantity": "10",
    "price": "100",
    "timestamp": "2026-01-01T10:00:00Z"
  }'

# BUY 10 @ 120
curl -s -X POST http://localhost:8000/trades \
  -H 'Content-Type: application/json' \
  -d '{
    "venue": "binance",
    "trade_id": "B-2",
    "account_id": "desk-1",
    "symbol": "BTC-USD",
    "side": "BUY",
    "quantity": "10",
    "price": "120",
    "timestamp": "2026-01-01T11:00:00Z"
  }'

# SELL 15 @ 150
curl -s -X POST http://localhost:8000/trades \
  -H 'Content-Type: application/json' \
  -d '{
    "venue": "binance",
    "trade_id": "S-1",
    "account_id": "desk-1",
    "symbol": "BTC-USD",
    "side": "SELL",
    "quantity": "15",
    "price": "150",
    "timestamp": "2026-01-01T12:00:00Z"
  }'
```

The SELL consumes the oldest BUY lots first:

    Quantity matched BUY lot        Calculation            Realized P&L
  ------------------ -------------- -------------------- --------------
                  10 `B-1` at 100   `10 × (150 − 100)`              500
                   5 `B-2` at 120   `5 × (150 − 120)`               150
                                    **Realized P&L**            **650**

Five units remain from `B-2` at a cost of 120 each. Before a mark price
is supplied, unrealized P&L is unavailable (`null`), rather than assumed
to be zero.

Set a mock mark price of 140:

``` bash
curl -s -X PUT http://localhost:8000/market-prices/BTC-USD \
  -H 'Content-Type: application/json' \
  -d '{"price": "140"}'

curl -s 'http://localhost:8000/pnl?account_id=desk-1'
```

Expected P&L values for this example:

-   Realized P&L: `650`
-   Unrealized P&L: `5 × (140 − 120) = 100`
-   Total P&L: `650 + 100 = 750`

These figures are before fees and other costs. Use a clean database or
unique trade IDs when replaying the example, because trade identity is
enforced by `(venue, trade_id)`.

## Idempotency and oversell behavior

### Idempotency

Trade identity is `(venue, trade_id)` and is enforced by a database
`UNIQUE` constraint.

-   Resubmitting the same trade with equivalent details returns `200`
    and does not apply the trade twice.
-   Reusing the same identity with different trade details returns
    `409 Conflict`.
-   Decimal values are compared by numeric value, so `"10"` and
    `"10.000"` are equivalent.
-   Timestamps are compared by instant, so `12:00Z` and `14:00+02:00`
    represent the same instant.

### Oversell prevention

If a SELL exceeds available inventory, the API returns `422` with an
`insufficient_inventory` error. The rejected trade does not partially
update the ledger. A rejected trade is not recorded, so the same trade
identity can be retried after inventory changes.

Concurrent SELLs against the same position are protected using
PostgreSQL row locking and transactional updates. See
[DESIGN.md](DESIGN.md) for the implementation rationale.

## Reconciliation

Reconciliation compares the internal ledger with data supplied in the
request. It does **not** connect to a live exchange and does **not**
modify or repair the ledger.

-   `POST /reconciliation/trades` reports matched records, records
    missing internally, internal records missing from the external
    snapshot, duplicate external identities, and conflicting trade
    details.
-   `POST /reconciliation/positions` compares expected quantities with
    internal open quantities and reports mismatches, missing positions,
    and duplicates.
-   Trade reconciliation is scoped to the specified venue. Optional
    `window_start` and `window_end` fields limit which internal trades
    are expected in the snapshot.
-   Snapshot records may omit `account_id`; when supplied, it is
    included in the comparison.
-   Reconciliation requests are capped at 10,000 records.

Review discrepancies before taking any corrective action; the service
deliberately leaves the ledger unchanged.

## Tests and code quality

Run the complete test suite:

``` bash
pytest
```

Run unit tests only (no database required):

``` bash
pytest tests/unit
```

Run integration tests (requires PostgreSQL and a correctly configured
`TEST_DATABASE_URL`):

``` bash
pytest tests/integration
```

The integration suite exercises the database-backed API, constraints,
transaction behavior, and concurrency. It applies the Alembic migration
to the test database and checks consistency between migrations and
models. If PostgreSQL is unavailable, integration tests may be skipped
with a reason rather than counted as passing.

Run linting and formatting checks:

``` bash
ruff check .
ruff format --check .
```

To apply formatting:

``` bash
ruff format .
```

### Local verification performed

The project was locally verified on Windows using Python 3.14.4 and
PostgreSQL 16.15. The reported checks were:

-   `pytest tests/unit`: 74 passed
-   `pytest`: 198 passed, with one third-party deprecation warning
-   `ruff check .`: passed
-   `ruff format --check .`: passed (41 files already formatted)
-   Manual API checks for health/readiness, duplicate-trade handling,
    conflicting duplicates, oversell prevention, and the FIFO P&L
    example

The Docker Compose configuration was not run during that local
verification. The environment used for verification differs from the
Python 3.11+ baseline described above, so compatibility should be
checked in the target deployment environment as well.

## Known limitations

-   **Long-only accounting:** short selling is rejected; derivatives,
    contract multipliers, margin, liquidation, and funding are not
    implemented.
-   **Fees excluded:** P&L is before fees, rebates, and funding.
-   **Acceptance-order FIFO:** trades are matched in the order they are
    accepted, not reordered by event timestamp. A late-arriving trade
    with an earlier timestamp does not trigger historical rematching.
-   **Mock mark prices:** mark prices are manually supplied, stored per
    symbol, and have no price history.
-   **No authentication or rate limiting:** access control and abuse
    prevention are outside the assignment's scope.
-   **No pagination for positions/P&L:** trade listing is paginated, but
    positions and P&L summaries are not.
-   **Read-only reconciliation:** discrepancies are reported but not
    automatically corrected.
-   **No batch ingestion endpoint:** trades are submitted one request at
    a time.
-   **Single quote-currency assumption:** aggregate P&L across symbols
    assumes their quote currencies are compatible.
-   **Deployment scope:** the Docker Compose configuration is provided
    but was not run as part of the author's local verification.
