# Trade Recorder & P&L Service

A small backend that ingests trades from multiple venues, tracks long-only positions and
reports **FIFO** realized / unrealized P&L. Ingestion is **idempotent**, SELLs are checked
against inventory (**oversell rejected**), and a read-only **reconciliation** endpoint
compares the internal ledger with a venue snapshot you supply.

Built as a take-home assignment. It is a modular monolith (FastAPI + PostgreSQL), not a
production trading system - see [Known limitations](#known-limitations).

* Design decisions, data model, concurrency strategy: [DESIGN.md](DESIGN.md)
* How AI assistance was used: [AI_USAGE.md](AI_USAGE.md)

## Scope and assumptions

| Topic | Decision |
|---|---|
| Venues | Any string; trades are supplied by the caller (mock exchange trades). No exchange integration. |
| Trade identity | `(venue, trade_id)`. Venue is case-insensitive (stored lower-case). |
| Position key | `(account_id, venue, symbol)`. Inventory is never netted across accounts, venues or symbols. |
| Position policy | **Long-only.** A SELL larger than the open position is rejected (HTTP 422). Short selling is not supported. |
| Accounting | **FIFO.** SELLs consume the oldest BUY lots first, in *acceptance order*. |
| Prices | `Decimal` everywhere; JSON decimals are **strings** (`"100.5"`). Up to 18 integer + 18 decimal digits. |
| Currency | P&L is in the symbol's quote currency (USD for `BTC-USD`). Summing across symbols assumes one common quote currency. |
| Mark price | Supplied by you via `PUT /market-prices/{symbol}` (a **mock** input, not a market-data feed). If missing, unrealized P&L is `null` - never 0, never the last trade price. |
| Fees | Out of scope. P&L is before fees, funding, rebates. |
| Timestamps | ISO-8601 **string with a UTC offset** (`2026-01-01T12:00:00Z`). Naive timestamps and bare numbers are rejected. |
| Default account | `account_id` defaults to `"default"` when omitted. |
| Auth | None (out of scope). |

## Tech stack

Python 3.11+ (developed and tested on 3.12), FastAPI, Pydantic v2, SQLAlchemy 2.x
(sync, psycopg 3), Alembic, PostgreSQL 16, pytest + HTTPX, Ruff, Docker Compose (DB only).

## Architecture

```
app/
  main.py            FastAPI app factory + router registration
  config.py          env-based settings (DATABASE_URL)
  database.py        engine + per-request Session
  models.py          SQLAlchemy tables: trades, positions, lots, lot_matches, market_prices
  schemas.py         Pydantic request/response models and validation rules
  errors.py          domain exceptions
  money.py           Decimal context + formatting
  api/               thin HTTP routers + error handlers (no business logic)
  services/
    fifo.py          PURE FIFO matching / P&L maths (no DB)
    ingestion.py     idempotency + locking + one-transaction apply
    portfolio.py     positions, P&L, mock market prices
    trades.py        trade queries
    reconciliation.py  read-only snapshot comparison
    comparison.py    shared "are these the same trade?" logic
migrations/          Alembic (0001 = full schema)
tests/unit/          pure tests - no database needed
tests/integration/   PostgreSQL-backed API, constraint and concurrency tests
```

## Prerequisites

* Python 3.11+
* PostgreSQL 16 - easiest via Docker (`docker compose`), or any local PostgreSQL

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                 # defaults match docker-compose.yml
```

### PostgreSQL (Docker Compose)

```bash
docker compose up -d db              # starts postgres:16 on localhost:5432
```

This creates database `trades` (the app) and, on the **first** start of an empty volume,
`trades_test` (the test suite). If your volume already existed, create the test database
once: `docker compose exec db createdb -U trader trades_test`.

> `docker-compose.yml` was not executed in the environment where this project was authored
> (no Docker there). The same credentials and database names were verified against a
> locally installed PostgreSQL 16. Using your own PostgreSQL? Create both databases and set
> `DATABASE_URL` / `TEST_DATABASE_URL`.

### Migrations

```bash
alembic upgrade head                 # creates all tables in DATABASE_URL
```

### Run the service

```bash
uvicorn app.main:app --reload
```

Interactive API docs: <http://localhost:8000/docs> (OpenAPI JSON at `/openapi.json`).

### Environment variables

| Variable | Used by | Default (`.env.example`) |
|---|---|---|
| `DATABASE_URL` | app, Alembic | `postgresql+psycopg://trader:trader@localhost:5432/trades` |
| `TEST_DATABASE_URL` | tests only | `postgresql+psycopg://trader:trader@localhost:5432/trades_test` |

The credentials are local-development defaults. Never commit a real `.env`.
**The test database name must end in `_test`**: tests truncate every table, and the suite
aborts rather than touch any other database.

## Tests

```bash
pytest                    # everything (needs PostgreSQL for tests/integration)
pytest tests/unit         # pure tests, no database required
pytest tests/integration  # PostgreSQL-backed: API, constraints, rollback, concurrency
```

If PostgreSQL is unreachable, the integration tests are reported as **SKIPPED** with a
reason (not as passed). The suite applies the Alembic migration to the test database, so
the migration itself is exercised, and a test asserts migrations and models match.

Concurrency tests use real parallel database sessions (threads released by a barrier,
repeated over several rounds).

## Lint / format

```bash
ruff check .
ruff format --check .     # `ruff format .` to apply
```

## API

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness (no database access) |
| `GET /health/ready` | Readiness: runs `SELECT 1`; `503` if the database is down |
| `POST /trades` | Record a trade. `201` accepted, `200` identical duplicate, `409` conflicting duplicate, `422` invalid or oversell |
| `GET /trades` | List trades (acceptance order). Filters: `venue`, `symbol`, `account_id`; pagination: `limit` (1-500, default 50), `offset` |
| `GET /trades/{venue}/{trade_id}` | One trade by its venue identity (`404` if unknown) |
| `GET /positions` | Positions with open FIFO lots, average cost, realized/unrealized/total P&L. Filters: `account_id`, `venue`, `symbol` |
| `GET /pnl` | P&L summary. Same filters |
| `PUT /market-prices/{symbol}` | Set the **mock** mark price |
| `POST /reconciliation/trades` | Trade-level reconciliation against a supplied snapshot (read-only) |
| `POST /reconciliation/positions` | Position-level reconciliation against supplied expected quantities (read-only) |

All errors share one shape: `{"error": {"code": "...", "message": "...", "details": ...}}`.
Codes: `validation_error` (422), `trade_conflict` (409), `insufficient_inventory` (422),
`not_found` (404), `database_unavailable` (503), `internal_error` (500).

## Example: FIFO P&L

The following calls were run against a real instance (venue `binance`, account `desk-1`).

```bash
# BUY 10 @ 100, BUY 10 @ 120
curl -s -X POST localhost:8000/trades -H 'Content-Type: application/json' -d '{
  "venue":"binance","trade_id":"B-1","account_id":"desk-1","symbol":"BTC-USD",
  "side":"BUY","quantity":"10","price":"100","timestamp":"2026-01-01T10:00:00Z"}'
curl -s -X POST localhost:8000/trades -H 'Content-Type: application/json' -d '{
  "venue":"binance","trade_id":"B-2","account_id":"desk-1","symbol":"BTC-USD",
  "side":"BUY","quantity":"10","price":"120","timestamp":"2026-01-01T11:00:00Z"}'
# SELL 15 @ 150
curl -s -X POST localhost:8000/trades -H 'Content-Type: application/json' -d '{
  "venue":"binance","trade_id":"S-1","account_id":"desk-1","symbol":"BTC-USD",
  "side":"SELL","quantity":"15","price":"150","timestamp":"2026-01-01T12:00:00Z"}'
```

First response (`201`):

```json
{"status":"accepted","trade":{"id":1,"venue":"binance","trade_id":"B-1","account_id":"desk-1",
 "symbol":"BTC-USD","side":"BUY","quantity":"10","price":"100",
 "timestamp":"2026-01-01T10:00:00Z","created_at":"2026-10-21T05:46:51.204533Z"}}
```

The SELL consumes the oldest lot first:

| Matched | Lot | Calculation | Realized |
|---|---|---|---|
| 10 | B-1 @ 100 | 10 x (150 - 100) | 500 |
| 5 | B-2 @ 120 | 5 x (150 - 120) | 150 |
| | | **Realized total** | **650** |

5 units remain from B-2 at cost 120. Before a mark price exists, unrealized P&L is
explicitly unavailable:

```bash
curl -s localhost:8000/pnl
# {"realized_pnl":"650","unrealized_pnl":null,"total_pnl":null,
#  "missing_mark_prices":["BTC-USD"],"position_count":1}
```

Supply a mark price of 140:

```bash
curl -s -X PUT localhost:8000/market-prices/BTC-USD -H 'Content-Type: application/json' \
  -d '{"price":"140"}'
curl -s localhost:8000/pnl
# {"realized_pnl":"650","unrealized_pnl":"100","total_pnl":"750",
#  "missing_mark_prices":[],"position_count":1}
```

Unrealized = 5 x (140 - 120) = **100**; total = 650 + 100 = **750** (before fees).
`GET /positions?account_id=desk-1` returns:

```json
[{"account_id":"desk-1","venue":"binance","symbol":"BTC-USD","quantity":"5","average_cost":"120",
  "open_lots":[{"trade_id":"B-2","original_quantity":"10","remaining_quantity":"5","price":"120"}],
  "realized_pnl":"650","mark_price":"140","unrealized_pnl":"100","total_pnl":"750"}]
```

## Idempotency and oversell behavior

**Idempotency.** A trade's identity is `(venue, trade_id)`, enforced by a database UNIQUE
constraint (not an in-memory check). Resubmitting the exact same trade returns `200` with
`"status":"duplicate"` and the original record; nothing is applied twice. Resubmitting the
same identity with different details (quantity, price, side, symbol, account, timestamp)
returns `409`:

```json
{"error":{"code":"trade_conflict","message":"Trade 'B-1' on venue 'binance' was already recorded with different details",
 "details":{"internal_id":1,"differences":[{"field":"quantity","internal":"10","external":"11"}]}}}
```

Decimals compare by value (`10` equals `10.000`) and timestamps by instant
(`12:00Z` equals `14:00+02:00`), so harmless reformatting is still a duplicate.

**Oversell.** Selling more than the open position returns `422` and persists nothing
(no trade row, no lot change, no position row):

```json
{"error":{"code":"insufficient_inventory",
 "message":"Cannot sell 25: only 20 available (long-only policy; short selling is not supported)",
 "details":{"requested":"25","available":"20"}}}
```

A rejected trade is never recorded, so retrying the same `trade_id` after the position grows
works normally. Concurrent SELLs on one position are serialized with a PostgreSQL row lock
(see [DESIGN.md](DESIGN.md)); this was also checked with 4 separate server processes.

## Example: reconciliation

Compare a venue snapshot with the ledger (the snapshot below disagrees on `B-2`'s price,
omits `S-1`, and contains an unknown trade `X-9`):

```bash
curl -s -X POST localhost:8000/reconciliation/trades -H 'Content-Type: application/json' -d '{
  "venue":"binance",
  "records":[
    {"trade_id":"B-1","symbol":"BTC-USD","side":"BUY","quantity":"10","price":"100","timestamp":"2026-01-01T10:00:00Z"},
    {"trade_id":"B-2","symbol":"BTC-USD","side":"BUY","quantity":"10","price":"121","timestamp":"2026-01-01T11:00:00Z"},
    {"trade_id":"X-9","symbol":"BTC-USD","side":"BUY","quantity":"1","price":"99","timestamp":"2026-01-01T09:00:00Z"}]}'
```

```json
{"venue":"binance","records_examined":3,"internal_trades_examined":3,
 "summary":{"matched":1,"missing_internal":1,"missing_external":1,"duplicate_external":0,
            "conflicts":1,"has_discrepancies":true},
 "missing_internal":[{"trade_id":"X-9","account_id":null,"symbol":"BTC-USD","side":"BUY","quantity":"1","price":"99","timestamp":"2026-01-01T09:00:00Z"}],
 "missing_external":[{"trade_id":"S-1","account_id":"desk-1","symbol":"BTC-USD","side":"SELL","quantity":"15","price":"150","timestamp":"2026-01-01T12:00:00Z"}],
 "duplicate_external":[],
 "conflicts":[{"trade_id":"B-2","differences":[{"field":"price","internal":"120","external":"121"}]}]}
```

* The comparison is against **the snapshot you supply**, not a live exchange.
* Scope is the snapshot's `venue`. Optional `window_start` / `window_end` limit which
  *internal* trades are expected to appear in the snapshot.
* `account_id` in snapshot records is optional and compared only when present.
* Reconciliation never writes: it runs in a `READ ONLY` transaction and does not fix
  discrepancies. Duplicate external identities are reported (comparison uses the first copy;
  `identical:false` flags copies that disagree).
* `POST /reconciliation/positions` compares expected `(account_id, symbol)` quantities with
  internal open quantities and reports `quantity_mismatches`, `missing_internal`,
  `missing_external` and duplicates.

## Known limitations

* **Long-only, no fees, one quote currency.** Not a derivatives accounting system (no
  contract multipliers, funding, liquidation, margin).
* **FIFO follows acceptance order, not event time.** A trade that arrives late with an
  earlier timestamp is applied when accepted; history is not re-matched. Timestamps are
  stored and returned but do not drive matching.
* **Mark prices are mock inputs**, one per symbol (not per venue), with no history.
* **No authentication or authorization**, no rate limiting.
* `GET /positions` and `/pnl` are not paginated; reconciliation snapshots are capped at
  10,000 records per request.
* Reconciliation is read-only reporting; there is no repair workflow.
* Only one operation per request (no batch ingestion endpoint).
* Verified on Python 3.12 / PostgreSQL 16. `docker-compose.yml` has not been executed by the
  author of this repo. Running the tests prints one third-party deprecation warning
  (FastAPI's `TestClient` suggesting a different HTTP client package); it does not affect
  results.
