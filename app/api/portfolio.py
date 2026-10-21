from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas import MarketPriceIn, MarketPriceOut, PnlSummary, PositionOut
from app.services import portfolio

router = APIRouter(tags=["portfolio"])


@router.get("/positions", response_model=list[PositionOut], summary="Positions with FIFO lots")
def get_positions(
    session: SessionDep,
    account_id: str | None = None,
    venue: str | None = None,
    symbol: str | None = None,
) -> list[PositionOut]:
    return portfolio.list_positions(session, account_id=account_id, venue=venue, symbol=symbol)


@router.get("/pnl", response_model=PnlSummary, summary="Realized / unrealized P&L summary")
def get_pnl(
    session: SessionDep,
    account_id: str | None = None,
    venue: str | None = None,
    symbol: str | None = None,
) -> PnlSummary:
    return portfolio.pnl_summary(session, account_id=account_id, venue=venue, symbol=symbol)


@router.put(
    "/market-prices/{symbol}",
    response_model=MarketPriceOut,
    summary="Set the MOCK mark price used for unrealized P&L",
    description="A supplied test input, not a market-data feed. Unrealized P&L is reported "
    "as null for any open position whose symbol has no price.",
)
def put_market_price(symbol: str, body: MarketPriceIn, session: SessionDep) -> MarketPriceOut:
    return MarketPriceOut.model_validate(portfolio.set_market_price(session, symbol, body.price))
