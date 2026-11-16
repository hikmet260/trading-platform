import unittest
from datetime import datetime, timedelta, timezone

from app.risk.controls import NewsEvent, RiskManager, TradeProposal
from app.engine.strategy_runtime import StrategyRuntime
from app.strategies.backtest import run_backtest
from app.strategies.rules import StrategyParseError, parse_strategy_prompt


class StrategyCompilerTests(unittest.TestCase):
    def test_maps_supported_prompt_to_validated_rule(self):
        rule = parse_strategy_prompt(
            "Buy EUR/USD on 15m when RSI(14) < 30 and EMA(9) crosses above EMA(21) "
            "risk 0.5% stop loss 20 pips take profit 40 pips"
        )
        self.assertEqual(rule.symbol, "EUR/USD")
        self.assertEqual(rule.risk_pct, 0.5)
        self.assertEqual(rule.pip_size, 0.0001)
        self.assertEqual([condition.kind for condition in rule.conditions], ["threshold", "cross"])

    def test_rejects_unsupported_or_ambiguous_language(self):
        with self.assertRaises(StrategyParseError):
            parse_strategy_prompt("Buy EUR/USD if it looks bullish risk 0.5% stop loss 20 pips take profit 40 pips")

    def test_rejects_risk_outside_compiler_limit(self):
        with self.assertRaises(StrategyParseError):
            parse_strategy_prompt("Buy EUR/USD on 1h when RSI(14) < 30 risk 2% stop loss 20 pips take profit 40 pips")


class RiskControlTests(unittest.TestCase):
    def setUp(self):
        self.risk = RiskManager(max_daily_drawdown_pct=3, news_blackout_minutes=15)
        self.risk.reset_day(10_000, "2026-11-14")
        self.trade = TradeProposal(
            symbol="EUR/USD", side="buy", entry_price=1.1, stop_loss=1.098,
            take_profit=1.104, risk_pct=0.5, account_equity=10_000,
        )

    def test_mandatory_stop_loss_and_direction_are_enforced(self):
        missing = self.trade.model_copy(update={"stop_loss": None})
        assessment = self.risk.validate(missing, kill_switch=False)
        self.assertFalse(assessment["approved"])
        self.assertTrue(any("stop-loss" in reason for reason in assessment["reasons"]))
        wrong_side = self.trade.model_copy(update={"stop_loss": 1.101})
        self.assertFalse(self.risk.validate(wrong_side, kill_switch=False)["approved"])

    def test_drawdown_breaker_and_news_window_reject(self):
        self.risk.record_realized_pnl(-301, 9_699, "2026-11-14")
        assessment = self.risk.validate(self.trade, kill_switch=False, now=datetime(2026, 11, 14, tzinfo=timezone.utc))
        self.assertIn("Daily drawdown circuit breaker is active", assessment["reasons"])

        event = NewsEvent("US CPI", datetime(2026, 11, 14, 0, 5, tzinfo=timezone.utc))
        news_assessment = RiskManager().validate(
            self.trade, kill_switch=False, events=[event], now=datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(any("US CPI" in reason for reason in news_assessment["reasons"]))

    def test_position_quantity_is_derived_from_risk_and_stop_distance(self):
        result = self.risk.validate(self.trade, kill_switch=False)
        self.assertTrue(result["approved"])
        self.assertAlmostEqual(result["risk_amount"], 50)
        self.assertAlmostEqual(result["quantity"], 25_000)


class MarketMarkPriceTests(unittest.TestCase):
    def test_uses_bid_ask_midpoint_when_mt5_last_is_zero(self):
        self.assertEqual(StrategyRuntime._tick_mark_price({"last": 0, "bid": 1.1, "ask": 1.1002}), 1.1001)

    def test_rejects_invalid_quote_when_last_is_unavailable(self):
        self.assertIsNone(StrategyRuntime._tick_mark_price({"last": 0, "bid": 1.2, "ask": 1.1}))


class BacktestTests(unittest.TestCase):
    def test_reports_win_rate_profit_factor_and_drawdown(self):
        rule = parse_strategy_prompt(
            "Buy BTC/USDT on 1m when PRICE > 0 risk 0.5% stop loss 10 pips take profit 20 pips"
        )
        candles = []
        for index in range(12):
            opening = 100 + index * 0.25
            candles.append({
                "timestamp": 1_700_000_000_000 + index * 60_000,
                "open": opening, "high": opening + 0.25, "low": opening,
                "close": opening + 0.2, "volume": 1,
            })
        report = run_backtest(rule, candles, starting_balance=10_000, fee_bps=0)
        self.assertGreater(report["trade_count"], 0)
        self.assertEqual(report["win_rate_pct"], 100)
        self.assertEqual(report["profit_factor"], "infinity")
        self.assertIn("max_drawdown_pct", report)
        self.assertGreater(report["ending_balance"], report["starting_balance"])

    def test_rejects_invalid_historical_candles(self):
        rule = parse_strategy_prompt(
            "Buy BTC/USDT on 1m when PRICE > 0 risk 0.5% stop loss 10 pips take profit 20 pips"
        )
        with self.assertRaises(ValueError):
            run_backtest(rule, [{"timestamp": index, "close": 1} for index in range(3)])


if __name__ == "__main__":
    unittest.main()
