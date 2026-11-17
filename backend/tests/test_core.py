import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.risk.controls import NewsEvent, RiskManager, TradeProposal
from app.engine.strategy_runtime import StrategyRuntime
from app.connectors.mt5 import MT5Connector
from app.main import extract_copilot_strategy_prompt
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

    def test_parses_client_tiered_lot_size_prompt(self):
        prompt = (
            "Buy & Sell EUR/USD on 15m when RSI(14) < 30 "
            "the same lot size 0.12 but The first position 0.12x3=0.36 2nd one also 0.12X6=0.72 "
            "stop loss 20 pips take profit 40 pips"
        )
        rule = parse_strategy_prompt(prompt)
        self.assertEqual(rule.side, "both")
        self.assertEqual(rule.symbol, "EUR/USD")
        self.assertEqual(rule.sizing_mode, "fixed_lots")
        self.assertEqual(rule.base_lot_size, 0.12)
        self.assertEqual(rule.tier_lots, (0.36, 0.72))
        self.assertEqual(rule.stop_loss_pips, 20)
        self.assertEqual(rule.take_profit_pips, 40)

    def test_parses_client_prompt_with_sizing_first_and_sl_tp_abbreviations(self):
        prompt = (
            "Buy & Sell the same lot size 0.12 but The first position 0.12x3=0.36 "
            "2nd one also 0.12X6=0.72 on BTC/USDT 1h when RSI < 30 sl 50 pips tp 100 pips"
        )
        rule = parse_strategy_prompt(prompt)
        self.assertEqual(rule.side, "both")
        self.assertEqual(rule.symbol, "BTC/USDT")
        self.assertEqual(rule.timeframe, "1h")
        self.assertEqual(rule.sizing_mode, "fixed_lots")
        self.assertEqual(rule.base_lot_size, 0.12)
        self.assertEqual(rule.tier_lots, (0.36, 0.72))
        self.assertEqual(rule.conditions[0].indicator, "rsi")
        self.assertEqual(rule.conditions[0].period, 14)
        self.assertEqual(rule.conditions[0].value, 30)
        self.assertEqual(rule.stop_loss_pips, 50)
        self.assertEqual(rule.take_profit_pips, 100)

    def test_parses_tiered_lot_size_shorthand(self):
        rule = parse_strategy_prompt(
            "Buy EUR/USD on 15m when RSI(14) < 30 "
            "lot size 0.12 tiers 0.36, 0.72 "
            "stop loss 20 pips take profit 40 pips"
        )
        self.assertEqual(rule.side, "buy")
        self.assertEqual(rule.sizing_mode, "fixed_lots")
        self.assertEqual(rule.base_lot_size, 0.12)
        self.assertEqual(rule.tier_lots, (0.36, 0.72))

    def test_parses_position_1_position_2_explicit_syntax(self):
        rule = parse_strategy_prompt(
            "Sell EUR/USD on 1h when RSI(14) > 70 "
            "lot size 0.12 position 1 0.36 position 2 0.72 "
            "stop loss 25 pips take profit 50 pips"
        )
        self.assertEqual(rule.side, "sell")
        self.assertEqual(rule.sizing_mode, "fixed_lots")
        self.assertEqual(rule.base_lot_size, 0.12)
        self.assertEqual(rule.tier_lots, (0.36, 0.72))


class CopilotResponseTests(unittest.TestCase):
    def test_extracts_strategy_prompt_without_truncating_decimal_risk(self):
        expected = (
            "BUY BTC/USDT on 1m when RSI(14) < 30 risk 0.5% "
            "stop loss 10 pips take profit 20 pips"
        )
        self.assertEqual(extract_copilot_strategy_prompt(expected), expected)


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
        self.assertAlmostEqual(result["quantity"], 0.25)

    def test_fixed_quantity_capital_protection_and_limits(self):
        # Entry 1.1, stop 1.098 -> stop_distance = 0.002
        # Normal fixed lot: 0.20 lots (notional 22,000 <= 3x equity 30,000)
        # 0.20 lots * 100,000 contract * 0.002 stop = $40 risk
        tier1_trade = self.trade.model_copy(update={"fixed_quantity": 0.20})
        res1 = self.risk.validate(tier1_trade, kill_switch=False)
        self.assertTrue(res1["approved"])
        self.assertAlmostEqual(res1["quantity"], 0.20)
        self.assertAlmostEqual(res1["risk_amount"], 40.0)

        # 200,000 lots would risk 400,000 account currency: far above the
        # 1% per-trade cap and the 3x notional cap.
        risky_trade = self.trade.model_copy(update={"fixed_quantity": 200_000})
        res2 = self.risk.validate(risky_trade, kill_switch=False)
        self.assertFalse(res2["approved"])
        self.assertTrue(any("per-trade limit" in r or "notional" in r for r in res2["reasons"]))

        # Colossal fixed quantity that exceeds entire equity (10,000)
        ruinous_trade = self.trade.model_copy(update={"fixed_quantity": 6_000_000})
        res3 = self.risk.validate(ruinous_trade, kill_switch=False)
        self.assertFalse(res3["approved"])
        self.assertTrue(any("exceeds" in r for r in res3["reasons"]))


class MarketMarkPriceTests(unittest.TestCase):
    def test_uses_bid_ask_midpoint_when_mt5_last_is_zero(self):
        self.assertEqual(StrategyRuntime._tick_mark_price({"last": 0, "bid": 1.1, "ask": 1.1002}), 1.1001)

    def test_rejects_invalid_quote_when_last_is_unavailable(self):
        self.assertIsNone(StrategyRuntime._tick_mark_price({"last": 0, "bid": 1.2, "ask": 1.1}))

    def test_strategy_signal_id_is_stable_and_side_scoped(self):
        identity = ("tenant", "strategy", "EURUSD", "1m", "1763472000000")
        first = StrategyRuntime.signal_order_id(*identity, "buy")
        repeated = StrategyRuntime.signal_order_id(*identity, "buy")
        opposite = StrategyRuntime.signal_order_id(*identity, "sell")
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, opposite)
        self.assertLessEqual(len(first), 80)


class MT5ProtectedOrderTests(unittest.IsolatedAsyncioTestCase):
    class FakeMT5:
        TRADE_ACTION_DEAL = 1
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        ORDER_TIME_GTC = 0
        ORDER_FILLING_IOC = 1
        ORDER_FILLING_FOK = 2
        SYMBOL_FILLING_IOC = 2
        SYMBOL_FILLING_FOK = 1
        TRADE_RETCODE_DONE = 10009
        TRADE_RETCODE_PLACED = 10008
        TRADE_RETCODE_DONE_PARTIAL = 10010
        ORDER_STATE_PLACED = 1
        ORDER_STATE_CANCELED = 2

        def __init__(self, result, check_retcode=0):
            self.result = result
            self.check_retcode = check_retcode
            self.request = None
            self.symbol_select = lambda *_: True
            self.symbol_info = lambda *_: SimpleNamespace(
                volume_step=0.1, volume_min=0.1, volume_max=5.0,
                digits=2, filling_mode=2,
            )
            self.symbol_info_tick = lambda *_: SimpleNamespace(ask=100.0, bid=99.9)
            self.order_check = lambda request: SimpleNamespace(retcode=self.check_retcode, comment="check rejected")
            self.order_send = self._send
            self.history_deals_get = lambda *_, **__: [SimpleNamespace(position_id=777)]
            self.orders_get = lambda **_: []
            self.history_orders_get = lambda **_: [SimpleNamespace(state=self.ORDER_STATE_PLACED)]
            self.last_error = lambda: "fake error"

        def _send(self, request):
            self.request = request
            return self.result

    async def test_partial_fill_records_actual_quantity_and_protective_prices(self):
        fake = self.FakeMT5(SimpleNamespace(
            retcode=10010, order=55, deal=66, price=100.0, volume=0.3, comment="partial",
        ))
        connector = MT5Connector()
        connector._mt5 = fake
        connector.state = "connected"

        result = await connector.execute_protected_market_order("EURUSD", "buy", 0.5, 99.0, 102.0)

        self.assertEqual(result["quantity"], 0.3)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["position_ticket"], "777")
        self.assertEqual(fake.request["sl"], 99.0)
        self.assertEqual(fake.request["tp"], 102.0)

    async def test_rejected_order_check_does_not_submit_order(self):
        fake = self.FakeMT5(
            SimpleNamespace(retcode=10009, order=55, deal=66, price=100.0, volume=0.5),
            check_retcode=10016,
        )
        fake.order_send = Mock()
        connector = MT5Connector()
        connector._mt5 = fake
        connector.state = "connected"

        with self.assertRaisesRegex(RuntimeError, "order check"):
            await connector.execute_protected_market_order("EURUSD", "buy", 0.5, 99.0, 102.0)

        fake.order_send.assert_not_called()

    async def test_pending_order_is_not_reported_as_filled(self):
        fake = self.FakeMT5(SimpleNamespace(
            retcode=10008, order=55, deal=0, price=0.0, volume=0.0, comment="placed",
        ))
        connector = MT5Connector()
        connector._mt5 = fake
        connector.state = "connected"

        result = await connector.execute_protected_market_order("EURUSD", "buy", 0.5, 99.0, 102.0)

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["quantity"], 0.0)

    async def test_broker_order_rejection_is_returned_as_failure(self):
        fake = self.FakeMT5(SimpleNamespace(
            retcode=10006, order=0, deal=0, price=0.0, volume=0.0, comment="invalid stops",
        ))
        connector = MT5Connector()
        connector._mt5 = fake
        connector.state = "connected"

        with self.assertRaisesRegex(RuntimeError, "rejected protected order"):
            await connector.execute_protected_market_order("EURUSD", "buy", 0.5, 99.0, 102.0)

    async def test_order_send_timeout_is_not_retried(self):
        fake = self.FakeMT5(SimpleNamespace(
            retcode=10009, order=55, deal=66, price=100.0, volume=0.5,
        ))
        calls = []

        def timeout_after_send(_request):
            calls.append("send")
            raise TimeoutError("simulated MT5 terminal timeout")

        fake.order_send = timeout_after_send
        connector = MT5Connector()
        connector._mt5 = fake
        connector.state = "connected"

        with self.assertRaisesRegex(TimeoutError, "simulated"):
            await connector.execute_protected_market_order("EURUSD", "buy", 0.5, 99.0, 102.0)

        self.assertEqual(calls, ["send"])

    async def test_mt5_order_status_reports_pending_and_canceled(self):
        fake = self.FakeMT5(SimpleNamespace(
            retcode=10009, order=55, deal=66, price=100.0, volume=0.5,
        ))
        connector = MT5Connector()
        connector._mt5 = fake
        connector.state = "connected"
        self.assertEqual(await connector.order_status("55"), "pending")

        fake.history_orders_get = lambda **_: [SimpleNamespace(state=fake.ORDER_STATE_CANCELED)]
        self.assertEqual(await connector.order_status("55"), "canceled")


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

    def test_multi_tier_lot_size_backtest_executes_sequential_positions(self):
        rule = parse_strategy_prompt(
            "Buy EUR/USD on 1m when PRICE > 0 lot size 0.12 tiers 0.36, 0.72 stop loss 1000 pips take profit 2000 pips"
        )
        self.assertEqual(rule.tier_lots, (0.36, 0.72))
        candles = []
        for index in range(5):
            opening = 1.1000 + index * 0.0002
            candles.append({
                "timestamp": 1_700_000_000_000 + index * 60_000,
                "open": opening, "high": opening + 0.0001, "low": opening - 0.0001,
                "close": opening + 0.0001, "volume": 1,
            })
        # 0.36 lots * 100,000 * 0.1 (1000 pips) = $36,000 risk; need equity ~$3.6M for 1% cap.
        # Use $4M starting balance so both tiers pass the 1% per-trade and 2% aggregate caps.
        report = run_backtest(rule, candles, starting_balance=4_000_000, fee_bps=0)
        self.assertGreater(report["trade_count"], 0)
        quantities = [t["quantity"] for t in report["trades"]]
        self.assertIn(0.36, quantities)
        self.assertIn(0.72, quantities)

    def test_rejects_invalid_historical_candles(self):
        rule = parse_strategy_prompt(
            "Buy BTC/USDT on 1m when PRICE > 0 risk 0.5% stop loss 10 pips take profit 20 pips"
        )
        with self.assertRaises(ValueError):
            run_backtest(rule, [{"timestamp": index, "close": 1} for index in range(3)])


class AuthCoreTests(unittest.TestCase):
    def test_password_hashing_and_verification(self):
        from app.core.auth import hash_password, verify_password
        raw = "SecurePassword123!"
        hashed = hash_password(raw)
        self.assertTrue(verify_password(raw, hashed))
        self.assertFalse(verify_password("WrongPassword123!", hashed))

    def test_token_issuance_and_verification(self):
        from app.core.auth import issue_access_token, verify_access_token
        secret = "0123456789abcdef0123456789abcdef"
        token = issue_access_token("user-1", "tenant-1", secret, ttl_seconds=300)
        claims = verify_access_token(token, secret)
        self.assertEqual(claims["sub"], "user-1")
        self.assertEqual(claims["tid"], "tenant-1")

    def test_token_rejects_tampered_or_invalid_secret(self):
        from app.core.auth import issue_access_token, verify_access_token
        secret = "0123456789abcdef0123456789abcdef"
        token = issue_access_token("user-1", "tenant-1", secret, ttl_seconds=300)
        with self.assertRaises(ValueError):
            verify_access_token(token, "different_secret_key_1234567890123456")


if __name__ == "__main__":
    unittest.main()
