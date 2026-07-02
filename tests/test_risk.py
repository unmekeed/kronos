from kronos_trader.config import RiskConfig
from kronos_trader.risk.manager import RiskManager
from kronos_trader.strategy.signal import Action, Signal


def long_signal(price=100.0, conf=0.9, ret=0.01):
    return Signal(action=Action.LONG, price=price, expected_return=ret, confidence=conf)


def make_manager(**overrides):
    cfg = RiskConfig(cooldown_after_loss_sec=0, **overrides)
    return RiskManager(cfg)


def test_position_sizing_risk_per_trade():
    rm = make_manager(risk_per_trade=0.01, stop_loss_pct=0.01, max_position_pct=1.0)
    decision = rm.evaluate(long_signal(price=100.0), balance=1000.0, open_positions=0)
    assert decision.allowed
    # 1% risk with 1% stop => full balance notional, capped by max_position_pct=100%
    assert decision.notional == 1000.0
    assert decision.size == 10.0
    assert decision.stop_price == 99.0
    assert decision.take_price == 102.0


def test_max_position_cap():
    rm = make_manager(risk_per_trade=0.05, stop_loss_pct=0.01, max_position_pct=0.25)
    decision = rm.evaluate(long_signal(), balance=1000.0, open_positions=0)
    assert decision.allowed
    assert decision.notional == 250.0  # capped at 25%


def test_min_order_rejected():
    rm = make_manager(min_order_usd=10.0, risk_per_trade=0.01,
                      stop_loss_pct=0.1, max_position_pct=0.05)
    decision = rm.evaluate(long_signal(), balance=50.0, open_positions=0)
    assert not decision.allowed
    assert "below exchange minimum" in decision.reason


def test_max_open_positions():
    rm = make_manager(max_open_positions=1)
    decision = rm.evaluate(long_signal(), balance=1000.0, open_positions=1)
    assert not decision.allowed
    assert "max open positions" in decision.reason


def test_short_disabled_on_spot():
    rm = make_manager(allow_short=False)
    signal = Signal(action=Action.SHORT, price=100.0,
                    expected_return=-0.01, confidence=0.9)
    decision = rm.evaluate(signal, balance=1000.0, open_positions=0)
    assert not decision.allowed
    assert "shorts disabled" in decision.reason


def test_daily_loss_limit_blocks_trading():
    rm = make_manager(max_daily_loss_pct=0.05)
    rm.register_trade_result(-60.0)  # more than 5% of 1000
    decision = rm.evaluate(long_signal(), balance=1000.0, open_positions=0)
    assert not decision.allowed
    assert "daily loss limit" in decision.reason


def test_kill_switch_on_drawdown():
    rm = make_manager(max_drawdown_pct=0.15)
    rm.update_equity(1000.0)
    rm.update_equity(840.0)  # -16%
    assert rm.kill_switch
    decision = rm.evaluate(long_signal(), balance=840.0, open_positions=0)
    assert not decision.allowed
    assert "kill switch" in decision.reason
    rm.reset_kill_switch()
    assert not rm.kill_switch
    assert rm.evaluate(long_signal(), balance=840.0, open_positions=0).allowed


def test_hold_signal_rejected():
    rm = make_manager()
    signal = Signal(action=Action.HOLD, price=100.0,
                    expected_return=0.0, confidence=0.0)
    assert not rm.evaluate(signal, balance=1000.0, open_positions=0).allowed
