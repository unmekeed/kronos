from kronos_trader.config import StrategyConfig
from kronos_trader.model.predictor import Forecast
from kronos_trader.strategy.signal import Action, make_signal
from kronos_trader.trading.positions import Position, PositionBook


def forecast(ret, conf, price=100.0):
    return Forecast(last_price=price, predicted_price=price * (1 + ret),
                    expected_return=ret, confidence=conf, horizon=12)


def test_long_signal():
    cfg = StrategyConfig(min_expected_return=0.001, min_confidence=0.5)
    assert make_signal(forecast(0.01, 0.9), cfg).action is Action.LONG


def test_short_signal():
    cfg = StrategyConfig(min_expected_return=0.001, min_confidence=0.5)
    assert make_signal(forecast(-0.01, 0.9), cfg).action is Action.SHORT


def test_hold_on_low_confidence():
    cfg = StrategyConfig(min_expected_return=0.001, min_confidence=0.5)
    assert make_signal(forecast(0.01, 0.3), cfg).action is Action.HOLD


def test_hold_on_small_move():
    cfg = StrategyConfig(min_expected_return=0.01, min_confidence=0.5)
    assert make_signal(forecast(0.001, 0.9), cfg).action is Action.HOLD


def test_position_pnl_long_short():
    long_pos = Position("BTC/USDT", "LONG", 2.0, 100.0, 99.0, 102.0, entry_fee=0.1)
    assert long_pos.unrealized_pnl(105.0) == 2.0 * 5.0 - 0.1
    short_pos = Position("BTC/USDT", "SHORT", 2.0, 100.0, 101.0, 98.0)
    assert short_pos.unrealized_pnl(95.0) == 2.0 * 5.0


def test_exit_hit_long():
    pos = Position("BTC/USDT", "LONG", 1.0, 100.0, 99.0, 102.0)
    assert pos.exit_hit(high=101.0, low=100.0) is None
    assert pos.exit_hit(high=101.0, low=98.9) == "stop_loss"
    assert pos.exit_hit(high=102.5, low=100.0) == "take_profit"
    # stop wins if both are inside the candle range (conservative)
    assert pos.exit_hit(high=103.0, low=98.0) == "stop_loss"


def test_exit_hit_short():
    pos = Position("BTC/USDT", "SHORT", 1.0, 100.0, 101.0, 98.0)
    assert pos.exit_hit(high=101.5, low=100.0) == "stop_loss"
    assert pos.exit_hit(high=100.5, low=97.9) == "take_profit"


def test_position_book():
    book = PositionBook()
    pos = Position("BTC/USDT", "LONG", 1.0, 100.0, 99.0, 102.0)
    book.open(pos)
    assert book.count() == 1
    assert book.get("BTC/USDT") is pos
    closed = book.close("BTC/USDT")
    assert closed is pos
    assert book.count() == 0
    assert book.close("BTC/USDT") is None
