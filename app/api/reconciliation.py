from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas import (
    PositionReconciliationReport,
    ReconcilePositionsRequest,
    ReconcileTradesRequest,
    TradeReconciliationReport,
)
from app.services import reconciliation

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.post(
    "/trades",
    response_model=TradeReconciliationReport,
    summary="Compare a supplied venue trade snapshot with the internal ledger (read-only)",
)
def reconcile_trades(
    body: ReconcileTradesRequest, session: SessionDep
) -> TradeReconciliationReport:
    return reconciliation.reconcile_trades(session, body)


@router.post(
    "/positions",
    response_model=PositionReconciliationReport,
    summary="Compare supplied expected positions with internal open quantities (read-only)",
)
def reconcile_positions(
    body: ReconcilePositionsRequest, session: SessionDep
) -> PositionReconciliationReport:
    return reconciliation.reconcile_positions(session, body)
