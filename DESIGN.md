# Design

This documents what is **implemented**. Anything not implemented is listed separately under
[Future improvements](#future-improvements).

## 1. Goals and non-goals

**Goals:** a small, correct, testable service that

* records trades from several venues, exactly once per `(venue, trade_id)`;
* keeps long-only positions and refuses oversells;
* reports FIFO realized and unrealized P&L with exact decimal arithmetic;
* reconciles the ledger against a supplied snapshot without modifying anything;
* stays consistent under concurrent requests, including across processes.

**Non-goals:** live exchange connectivity, market-data feeds, short selling, fees/funding,
derivatives accounting, authentication, event-time re-processing, high-throughput tuning.

A modular monolith with one PostgreSQL database is sufficient: correctness here depends on
transactions and constraints, which a single database provides directly. No queues, caches
or extra services were added because nothing in the requirements needs them.

## 2. Architecture

```
HTTP (FastAPI routers, thin)  ->  services (business rules)  ->  PostgreSQL (SQLAlchemy)
                                      |
                                  fifo.py: pure functions, no I/O
```

| Module | Responsibility |
|---|---|
| `api/*` | Parse/validate (Pydantic), call a service, map exceptions to HTTP. No business logic. |
| `schemas.py` | Input rules (positive decimals, aware ISO timestamps, normalization) and response shapes. |
| `services/fifo.py` | Pure FIFO matching, unrealized P&L, average cost. Unit-testable without a database. |
| `services/ingestion.py` | Idempotency, position locking, applying a trade in one transaction. |
| `services/portfolio.py` | Read model for positions/P&L; mock mark-price upsert. |
| `services/reconciliation.py` | Read-only comparison; pure `build_*_report` functions plus a thin DB loader. |
| `services/comparison.py` | The single definition of "same trade" used by idempotency *and* reconciliation. |
| `money.py` | 100-digit `Decimal` context and plain-string formatting. |

## 3. Trade lifecycle (`POST /trades`)

1. **Validate** (Pydantic). Invalid input never reaches the database.
2. **Begin one transaction.**
3. **Pre-check identity:** look up `(venue, trade_id)`. If found, return duplicate (identical
   payload) or raise conflict (different payload) - no locks taken, nothing written.
4. **Lock the position:** `INSERT ... ON CONFLICT DO NOTHING` the `(account, venue, symbol)`
   row, then `SELECT ... FOR UPDATE` it.
5. **Re-check identity under the lock** (a concurrent identical request may have committed
   while we waited).
6. **BUY:** insert the trade, insert a lot (`remaining = quantity`).
   **SELL:** load open lots oldest-first, run the *pure* `match_fifo` (raises
   `InsufficientInventoryError` *before any write*), insert the trade, decrement the
   consumed lots, insert one `lot_matches` row per consumed lot with its realized P&L.
7. **Commit.** Any exception (oversell, conflict, unexpected error) rolls back the whole
   transaction, so a rejected trade leaves no trade row, no lot change and no position row.

## 4. Data model

| Table | Purpose | Key constraints |
|---|---|---|
| `trades` | Immutable ledger of accepted trades. `id` (bigserial) is the **acceptance order**. | `UNIQUE (venue, trade_id)`; `CHECK quantity > 0`, `price > 0`, `side IN ('BUY','SELL')` |
| `positions` | One row per `(account_id, venue, symbol)`. Holds **no numbers**; exists to be row-locked. | `UNIQUE (account_id, venue, symbol)` |
| `lots` | FIFO inventory, one per accepted BUY. | `UNIQUE (trade_pk)`; `CHECK remaining >= 0`, `remaining <= original` |
| `lot_matches` | Audit trail: which lot each SELL consumed, quantity, prices, realized P&L. | `CHECK quantity > 0` |
| `market_prices` | Mock mark price per symbol. | `CHECK price > 0` |

Quantities and prices are `NUMERIC(36,18)`; per-match realized P&L is `NUMERIC(80,36)`
(price difference x quantity can need 36 decimal places). Timestamps are `timestamptz`.

**Single source of truth.** Position quantity is `SUM(lots.remaining_quantity)` and realized
P&L is `SUM(lot_matches.realized_pnl)`; neither is stored a second time, so they cannot
drift. `lots`/`lot_matches` are written in the same transaction as the trade that causes
them. (They are derived from the ledger by the matching rule, kept as materialized
inventory so SELLs don't replay history.)

### Trade identity

Identity is `(venue, trade_id)`: a venue's trade id names one fill. `account_id` is *part
of the payload*, not of the identity - the same venue trade id arriving for a different
account is a **conflict (409)**, not a second trade. Scoping identity by account would let
one real fill be double-counted into two accounts. Venue is lower-cased and symbol
upper-cased on input so `Binance`/`binance` and `btc-usd`/`BTC-USD` cannot create parallel
identities or positions.

## 5. Idempotency

* The guarantee is the DB `UNIQUE (venue, trade_id)` constraint.
* The application pre-check (steps 3 and 5) gives fast, deterministic answers for the
  normal case.
* If two requests race past the pre-check (which can only happen for the same identity on
  *different* positions, since same-position requests are serialized by the lock), the loser
  gets `IntegrityError` on `uq_trades_venue_trade_id`. Its transaction rolls back, a fresh
  transaction reads the winner's row and resolves to **duplicate** or **conflict** by the
  same comparison. Other integrity errors are not swallowed.
* Comparison: decimals by value, timestamps by instant, other fields exact.
* HTTP: `201` accepted, `200` duplicate (returns the original record), `409` conflict.

## 6. Concurrency and locking

A Python lock cannot protect state shared by several worker processes, so mutual exclusion
is done by PostgreSQL:

* Every trade touching a position first locks that position's row (`FOR UPDATE`). Trades on
  the same position therefore run one at a time; trades on different positions run in
  parallel.
* **First trade on a new position:** there is no row to lock yet, so we
  `INSERT ... ON CONFLICT DO NOTHING` (a concurrent inserter blocks until the other
  transaction ends, then does nothing) and then lock the row. Exactly one position row
  results.
* Default `READ COMMITTED` isolation: after acquiring the lock, subsequent statements see
  everything the previous lock holder committed, so a SELL always evaluates the up-to-date
  lots.
* Lot ids are assigned while holding the position lock, so for one position, id order equals
  commit order. FIFO sorts by lot id.

**Evidence** (see `tests/integration/test_concurrency.py`): real parallel sessions released
by a barrier, 5 rounds each - 8 simultaneous identical trades apply once; two simultaneous
SELLs of 6 against 10 accept exactly one; 10 simultaneous SELLs of 3 against 10 accept
exactly 3; same trade id racing on two accounts yields one winner and one 409 with no
leftover position row. Removing `FOR UPDATE` makes the oversell tests fail, and removing
the integrity-error handling makes the cross-account test fail (both mutation checks were
run by hand). Separately, a 4-process `uvicorn --workers 4` run with 12 parallel HTTP SELLs
of 3 against 10 units accepted exactly 3.

## 7. Position policy: long-only

BUY adds a lot; SELL consumes lots. A SELL above the open quantity raises
`InsufficientInventoryError` (HTTP 422) from the pure matcher before anything is written;
no clamping, no partial fill. Short selling is not supported. The pure `match_fifo` takes
lots as input and returns matches, so a future short-capable version would change that
function and the position model rather than the API or locking design.

Inventory is per `(account, venue, symbol)`: a BUY on one venue cannot cover a SELL on
another.

## 8. FIFO and P&L

* Lots are consumed oldest first; partial consumption leaves the remainder in the lot; one
  SELL can consume several lots.
* **Order:** FIFO follows **acceptance order** (lot id, an increasing sequence assigned
  under the position lock). Execution `timestamp` is stored but does not affect matching,
  so equal timestamps are not ambiguous. A late-arriving trade with an earlier timestamp is
  *not* back-dated and history is *not* re-matched; there is no event-time reprocessing.
* Realized per match: `(sell_price - lot_price) x matched_quantity`.
* Unrealized: `sum((mark - lot_price) x remaining_quantity)` over open lots.
* Total = realized + unrealized, before fees.
* Worked example (also a unit test and an API test): BUY 10@100, BUY 10@120, SELL 15@150
  -> realized `10x50 + 5x30 = 650`; 5 left @120; mark 140 -> unrealized `5x20 = 100`;
  total `750`.
* **Arithmetic:** `Decimal` only (no floats anywhere in app code). Inputs are limited to 18
  integer and 18 decimal digits, so every product/sum fits in the 100-digit context and is
  exact. The only rounding is `average_cost` (a division), rounded half-even to 18 dp and
  used for display only - P&L is always computed lot by lot. Decimals are serialized as
  strings. The unit tests include a case where float arithmetic gives `0.04000000000000001`
  and the service gives exactly `0.04`.

### Mark prices

`PUT /market-prices/{symbol}` stores a supplied positive price per symbol. It is a mock input
for the assignment, not a feed, and is not venue-specific. If a position has open inventory
and no mark price, `unrealized_pnl` and `total_pnl` are `null` (never 0, never the last
trade price), and `/pnl` lists the symbols in `missing_mark_prices` and returns a `null`
aggregate unrealized/total. A **flat** position (nothing open) has unrealized `0` because
there is nothing to mark.

## 9. Reconciliation

Read-only comparison against a **supplied** snapshot (not a live exchange).

* The transaction is `SET TRANSACTION READ ONLY`, so PostgreSQL itself rejects any write
  (tested). Tests also assert trades/positions/lots are unchanged after reconciling
  deliberately wrong snapshots.
* Trade-level algorithm (`build_trade_report`, pure): group external records by `trade_id`;
  for each id compare the first record to the internal trade for that venue.
  Categories: `missing_internal` (external only), `missing_external` (internal only, within
  scope), `duplicate_external` (id repeated; `identical:false` if copies disagree),
  `conflicts` (same id, differing fields listed one by one), plus `matched`.
* Scope: the snapshot's `venue`; optional `window_start/end` restrict only the *internal*
  side so older ledger trades are not reported as missing.
* `account_id` in snapshot records is optional and compared only if provided.
* Position-level reconciliation (`/reconciliation/positions`) compares expected
  `(account, symbol)` quantities with internal open quantities: `quantity_mismatches`,
  `missing_internal`, `missing_external`, duplicates. A snapshot quantity of 0 matches an
  absent internal position.
* Reports contain counts and identities, not a pass/fail boolean only.
* Nothing is repaired automatically.

## 10. Errors and HTTP status codes

Uniform body: `{"error": {"code", "message", "details"}}`.

| Situation | Status | `code` |
|---|---|---|
| Accepted new trade | 201 | - |
| Identical resubmission | 200 | - (`"status":"duplicate"`) |
| Malformed/invalid input (incl. naive timestamp) | 422 | `validation_error` |
| Same venue trade id, different details | 409 | `trade_conflict` |
| SELL exceeds open position | 422 | `insufficient_inventory` |
| Unknown trade / route | 404 | `not_found` / `http_error` |
| Database unreachable | 503 | `database_unavailable` |
| Unexpected error | 500 | `internal_error` (details logged, not returned) |

Oversell uses 422 (the request is well-formed but violates a business rule) and is told
apart from validation errors by `code`.

## 11. Testing strategy

* **Unit (no DB):** FIFO maths (spec scenario, partial lots, multi-lot, exact closure,
  loss, oversell, extreme precision, float-artifact case), input validation, the pure
  reconciliation comparator.
* **Integration (PostgreSQL):** API behavior and response schemas; idempotency/conflict;
  oversell with proof that nothing persisted; rollback when a failure is injected mid-SELL;
  DB constraints (unique, checks); migration matches models; exact decimal round trips;
  reconciliation incl. read-only guarantees; readiness when the DB is down.
* **Concurrency:** real parallel sessions, repeated rounds, as in section 6.
* The suite applies the Alembic migration to a disposable database whose name must end in
  `_test`. If PostgreSQL is unreachable, those tests are skipped with an explicit reason.
* No test depends on a live exchange.

## 12. Trade-offs and limitations

* `lots` is materialized inventory rather than recomputed from the ledger on each SELL: fast
  and simple to lock, at the cost of a derived structure that must be updated in the same
  transaction (it is, and constraints prevent negative/over-full lots).
* Acceptance-order FIFO is simple and deterministic but not a full event-time model.
* Same-position throughput is serialized by design; different positions are independent.
* Synchronous SQLAlchemy/psycopg with FastAPI's threadpool: simple and adequate here.
* The read endpoints load all lots of the selected positions; fine for this scale.
* No auth, no rate limiting, no batch ingestion, single quote currency, no fees.
* Future timestamps / clock skew are not validated.

## Future improvements

*Not implemented.*

* Event-time ordering with re-matching (replay) for out-of-order historical trades.
* Short positions (signed quantity, matching against open shorts).
* Fees and fee currency; multi-currency P&L; contract multipliers for derivatives.
* Batch ingestion and bulk reconciliation with paging; reconciliation against venue APIs.
* Authentication/authorization, rate limiting, structured logging and metrics.
* Pagination for positions; per-venue mark prices with timestamps and staleness checks.
