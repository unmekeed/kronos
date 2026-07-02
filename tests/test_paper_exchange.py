import pytest

from kronos_trader.config import ExchangeConfig
from kronos_trader.exchange.base import ExchangeError
from kronos_trader.exchange.paper import PaperExchange


def make_paper(price=100.0, balance=1000.0, fee=0.001, slippage=0.0):
    cfg = ExchangeConfig(taker_fee=fee, slippage=slippage)
    return PaperExchange(cfg, balance, price_fn=lambda _s: price)


def test_buy_reduces_balance_and_adds_holdings():
    ex = make_paper(price=100.0, balance=1000.0, fee=0.001)
    fill = ex.market_order("BTC/USDT", "buy", 1.0)
    assert fill.price == 100.0
    assert fill.fee == pytest.approx(0.1)
    assert ex.fetch_balance_usd() == pytest.approx(1000.0 - 100.0 - 0.1)
    assert ex.holdings["BTC"] == pytest.approx(1.0)


def test_sell_adds_balance():
    ex = make_paper(price=100.0, balance=1000.0, fee=0.001)
    ex.market_order("BTC/USDT", "buy", 1.0)
    ex.market_order("BTC/USDT", "sell", 1.0)
    # round trip loses two fees
    assert ex.fetch_balance_usd() == pytest.approx(1000.0 - 0.2)
    assert ex.holdings["BTC"] == pytest.approx(0.0)


def test_slippage_worsens_fill():
    ex = make_paper(price=100.0, slippage=0.001, fee=0.0)
    buy = ex.market_order("BTC/USDT", "buy", 1.0)
    sell = ex.market_order("BTC/USDT", "sell", 1.0)
    assert buy.price == pytest.approx(100.1)
    assert sell.price == pytest.approx(99.9)


def test_insufficient_balance():
    ex = make_paper(price=100.0, balance=50.0)
    with pytest.raises(ExchangeError, match="insufficient"):
        ex.market_order("BTC/USDT", "buy", 1.0)


def test_zero_size_rejected():
    ex = make_paper()
    with pytest.raises(ExchangeError):
        ex.market_order("BTC/USDT", "buy", 0.0)
