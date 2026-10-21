"""Read-only reconciliation of the internal ledger against a SUPPLIED snapshot.

The comparison is against the batch the caller passes in, not a live exchange API.
Nothing here inserts, updates or deletes: the transaction is declared READ ONLY, so the
database itself would reject any write.

Rules:
  * Duplicate external identities are reported; comparison uses the FIRST occurrence.
    `identical=False` on a duplicate flags copies that disagree with each other.
  * The optional window narrows only the INTERNAL side (so older internal trades are not
    reported as "missing from the snapshot").
"""

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Lot, Position, Trade
from app.money import ZERO
from app.schemas import (
    DuplicateExternal,
    ExternalPosition,
    ExternalTradeRecord,
    PositionMismatch,
    PositionReconciliationReport,
    PositionReconciliationSummary,
    ReconcilePositionsRequest,
    ReconcileTradesRequest,
    TradeConflict,
    TradeReconciliationReport,
    TradeReconciliationSummary,
    TradeRecordOut,
)
from app.services.comparison import trade_differences


# ------------------------------------------------------------------------- trades
def reconcile_trades(
    session: Session, request: ReconcileTradesRequest
) -> TradeReconciliationReport:
    _begin_read_only(session)
    conditions = [Trade.venue == request.venue]
    if request.window_start:
        conditions.append(Trade.timestamp >= request.window_start)
    if request.window_end:
        conditions.append(Trade.timestamp <= request.window_end)
    internal = session.scalars(select(Trade).where(*conditions).order_by(Trade.id)).all()
    return build_trade_report(request.venue, internal, request.records)


def build_trade_report(
    venue: str, internal: Sequence[Trade], external: Sequence[ExternalTradeRecord]
) -> TradeReconciliationReport:
    """Pure comparison (no database): unit-testable on its own."""
    internal_by_id = {t.trade_id: t for t in internal}
    groups: dict[str, list[ExternalTradeRecord]] = defaultdict(list)
    for record in external:
        groups[record.trade_id].append(record)

    missing_internal: list[TradeRecordOut] = []
    conflicts: list[TradeConflict] = []
    duplicates: list[DuplicateExternal] = []
    matched = 0

    for trade_id, records in groups.items():
        first = records[0]
        if len(records) > 1:
            duplicates.append(
                DuplicateExternal(
                    identity=trade_id,
                    occurrences=len(records),
                    identical=all(not _differs(first, other) for other in records[1:]),
                )
            )
        trade = internal_by_id.get(trade_id)
        if trade is None:
            missing_internal.append(_record_out(first))
            continue
        differences = trade_differences(trade, first, compare_account=first.account_id is not None)
        if differences:
            conflicts.append(TradeConflict(trade_id=trade_id, differences=differences))
        else:
            matched += 1

    missing_external = [_record_out(t) for t in internal if t.trade_id not in groups]
    summary = TradeReconciliationSummary(
        matched=matched,
        missing_internal=len(missing_internal),
        missing_external=len(missing_external),
        duplicate_external=len(duplicates),
        conflicts=len(conflicts),
        has_discrepancies=bool(missing_internal or missing_external or duplicates or conflicts),
    )
    return TradeReconciliationReport(
        venue=venue,
        records_examined=len(external),
        internal_trades_examined=len(internal),
        summary=summary,
        missing_internal=missing_internal,
        missing_external=missing_external,
        duplicate_external=duplicates,
        conflicts=conflicts,
    )


def _differs(a: ExternalTradeRecord, b: ExternalTradeRecord) -> bool:
    return bool(trade_differences(a, b, compare_account=True))


def _record_out(trade: Trade | ExternalTradeRecord) -> TradeRecordOut:
    return TradeRecordOut(
        trade_id=trade.trade_id,
        account_id=trade.account_id,
        symbol=trade.symbol,
        side=getattr(trade.side, "value", trade.side),
        quantity=trade.quantity,
        price=trade.price,
        timestamp=trade.timestamp,
    )


# ------------------------------------------------------------------------- positions
def reconcile_positions(
    session: Session, request: ReconcilePositionsRequest
) -> PositionReconciliationReport:
    _begin_read_only(session)
    rows = session.execute(
        select(Position.account_id, Position.symbol, func.sum(Lot.remaining_quantity))
        .join(Lot, Lot.position_id == Position.id)
        .where(Position.venue == request.venue)
        .group_by(Position.account_id, Position.symbol)
    ).all()
    internal = {(account, symbol): qty for account, symbol, qty in rows if qty > 0}
    return build_position_report(request.venue, internal, request.positions)


def build_position_report(
    venue: str,
    internal: dict[tuple[str, str], Decimal],
    external: Sequence[ExternalPosition],
) -> PositionReconciliationReport:
    """Pure comparison. `internal` maps (account_id, symbol) -> open quantity (> 0)."""
    groups: dict[tuple[str, str], list[ExternalPosition]] = defaultdict(list)
    for record in external:
        groups[(record.account_id, record.symbol)].append(record)

    mismatches: list[PositionMismatch] = []
    missing_internal: list[PositionMismatch] = []
    missing_external: list[PositionMismatch] = []
    duplicates: list[DuplicateExternal] = []
    matched = 0

    for key, records in groups.items():
        account, symbol = key
        first = records[0]
        if len(records) > 1:
            duplicates.append(
                DuplicateExternal(
                    identity=f"{account}/{symbol}",
                    occurrences=len(records),
                    identical=all(r.quantity == first.quantity for r in records[1:]),
                )
            )
        ours = internal.get(key, ZERO)
        entry = PositionMismatch(
            account_id=account,
            symbol=symbol,
            internal_quantity=ours,
            external_quantity=first.quantity,
        )
        if ours == first.quantity:
            matched += 1
        elif key not in internal:
            missing_internal.append(entry)
        else:
            mismatches.append(entry)

    for (account, symbol), ours in internal.items():
        if (account, symbol) not in groups:
            missing_external.append(
                PositionMismatch(
                    account_id=account,
                    symbol=symbol,
                    internal_quantity=ours,
                    external_quantity=ZERO,
                )
            )

    summary = PositionReconciliationSummary(
        matched=matched,
        quantity_mismatches=len(mismatches),
        missing_internal=len(missing_internal),
        missing_external=len(missing_external),
        duplicate_external=len(duplicates),
        has_discrepancies=bool(mismatches or missing_internal or missing_external or duplicates),
    )
    return PositionReconciliationReport(
        venue=venue,
        records_examined=len(external),
        summary=summary,
        quantity_mismatches=mismatches,
        missing_internal=missing_internal,
        missing_external=sorted(missing_external, key=lambda m: (m.account_id, m.symbol)),
        duplicate_external=duplicates,
    )


def _begin_read_only(session: Session) -> None:
    """Must be the first statement of the transaction; PostgreSQL then rejects any write."""
    session.execute(text("SET TRANSACTION READ ONLY"))
