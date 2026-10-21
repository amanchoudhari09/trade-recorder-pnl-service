from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import SessionDep
from app.schemas import ErrorResponse, IngestResponse, TradeIn, TradeList, TradeOut
from app.services import trades as trade_queries
from app.services.ingestion import ingest_trade

router = APIRouter(prefix="/trades", tags=["trades"])


@router.post(
    "",
    response_model=IngestResponse,
    status_code=201,
    summary="Record a trade (idempotent on venue + trade_id)",
    responses={
        200: {"model": IngestResponse, "description": "Identical trade already recorded"},
        409: {"model": ErrorResponse, "description": "Same venue trade id, different details"},
        422: {"model": ErrorResponse, "description": "Invalid payload, or SELL exceeds position"},
    },
)
def post_trade(body: TradeIn, response: Response, session: SessionDep) -> IngestResponse:
    result = ingest_trade(session, body)
    if result.duplicate:
        response.status_code = 200
    return IngestResponse(
        status="duplicate" if result.duplicate else "accepted",
        trade=TradeOut.model_validate(result.trade),
    )


@router.get("", response_model=TradeList, summary="List trades in acceptance order")
def get_trades(
    session: SessionDep,
    venue: str | None = None,
    symbol: str | None = None,
    account_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TradeList:
    items, total = trade_queries.list_trades(
        session, venue=venue, symbol=symbol, account_id=account_id, limit=limit, offset=offset
    )
    return TradeList(
        items=[TradeOut.model_validate(t) for t in items], total=total, limit=limit, offset=offset
    )


@router.get(
    "/{venue}/{trade_id:path}",
    response_model=TradeOut,
    summary="Get one trade by its venue identity",
    responses={404: {"model": ErrorResponse}},
)
def get_trade(venue: str, trade_id: str, session: SessionDep) -> TradeOut:
    return TradeOut.model_validate(trade_queries.get_trade(session, venue, trade_id))
