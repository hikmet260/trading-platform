from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuleCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["threshold", "cross"]
    indicator: Literal["rsi", "ema", "sma", "close"]
    operator: Literal["<", "<=", ">", ">=", "crosses_above", "crosses_below"]
    period: int | None = Field(default=None, ge=2, le=500)
    comparison_indicator: Literal["ema", "sma"] | None = None
    comparison_period: int | None = Field(default=None, ge=2, le=500)
    value: float | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "RuleCondition":
        if self.kind == "threshold":
            if self.operator not in {"<", "<=", ">", ">="} or self.value is None:
                raise ValueError("Threshold condition requires a comparison operator and numeric value")
            if self.comparison_indicator is not None or self.comparison_period is not None:
                raise ValueError("Threshold condition cannot specify a comparison indicator")
            if self.indicator == "close" and self.period is not None:
                raise ValueError("Close condition cannot have an indicator period")
            if self.indicator != "close" and self.period is None:
                raise ValueError("Indicator condition requires a period")
            if self.indicator == "rsi" and not 0 <= self.value <= 100:
                raise ValueError("RSI threshold must be between 0 and 100")
        else:
            if self.indicator not in {"ema", "sma"} or self.operator not in {"crosses_above", "crosses_below"}:
                raise ValueError("Cross condition requires EMA or SMA and a crossing operator")
            if self.period is None or self.comparison_period is None or self.comparison_indicator is None:
                raise ValueError("Cross condition requires two moving average periods")
            if self.indicator == self.comparison_indicator and self.period == self.comparison_period:
                raise ValueError("A moving average cannot cross itself")
            if self.value is not None:
                raise ValueError("Cross condition cannot contain a numeric value")
        return self


class StrategyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    side: Literal["buy", "sell"]
    symbol: str = Field(min_length=3, max_length=40, pattern=r"^[A-Z0-9/:_-]+$")
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    conditions: tuple[RuleCondition, ...] = Field(min_length=1, max_length=5)
    risk_pct: float = Field(gt=0, le=1.0, description="Percent of equity at risk per paper trade")
    stop_loss_pips: float = Field(gt=0, le=10_000)
    take_profit_pips: float = Field(gt=0, le=50_000)
    pip_size: float


class StrategyParseError(ValueError):
    pass


_PROMPT = re.compile(
    r"^(?P<side>buy|sell)\s+(?P<symbol>[A-Z0-9/:_-]+)\s+on\s+"
    r"(?P<timeframe>1m|5m|15m|30m|1h|4h|1d)\s+when\s+(?P<conditions>.+?)\s+"
    r"risk\s+(?P<risk>\d+(?:\.\d+)?)\s*%\s+stop\s+loss\s+"
    r"(?P<stop>\d+(?:\.\d+)?)\s+pips\s+take\s+profit\s+(?P<target>\d+(?:\.\d+)?)\s+pips\.?$",
    re.IGNORECASE,
)
_THRESHOLD = re.compile(r"^(RSI|EMA|SMA|PRICE|CLOSE)(?:\((\d+)\))?\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)$", re.I)
_CROSS = re.compile(r"^(EMA|SMA)\((\d+)\)\s+crosses\s+(above|below)\s+(EMA|SMA)\((\d+)\)$", re.I)


def parse_strategy_prompt(prompt: str) -> StrategyRule:
    """Parse the supported English strategy grammar without an LLM or fuzzy mapping."""
    normalized = " ".join(prompt.strip().split())
    match = _PROMPT.fullmatch(normalized)
    if not match:
        raise StrategyParseError(
            "Unsupported prompt. Use: BUY EUR/USD on 15m when RSI(14) < 30 AND "
            "EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips"
        )

    conditions: list[RuleCondition] = []
    for clause in re.split(r"\s+and\s+", match.group("conditions"), flags=re.I):
        threshold = _THRESHOLD.fullmatch(clause.strip())
        cross = _CROSS.fullmatch(clause.strip())
        if threshold:
            name, period, operator, value = threshold.groups()
            indicator = {"price": "close", "close": "close"}.get(name.lower(), name.lower())
            if indicator != "close" and period is None:
                raise StrategyParseError(f"{name.upper()} requires a period, for example {name.upper()}(14)")
            if indicator == "close" and period is not None:
                raise StrategyParseError("PRICE/CLOSE does not take a period")
            conditions.append(RuleCondition(
                kind="threshold", indicator=indicator, period=int(period) if period else None,
                operator=operator, value=float(value),
            ))
        elif cross:
            fast_kind, fast_period, direction, slow_kind, slow_period = cross.groups()
            conditions.append(RuleCondition(
                kind="cross", indicator=fast_kind.lower(), period=int(fast_period),
                operator=f"crosses_{'above' if direction.lower() == 'above' else 'below'}",
                comparison_indicator=slow_kind.lower(), comparison_period=int(slow_period),
            ))
        else:
            raise StrategyParseError(f"Unsupported condition '{clause.strip()}'; no rule was created")

    symbol = match.group("symbol").upper()
    pair = symbol.split("/")
    fx_currencies = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
    is_fx_pair = len(pair) == 2 and pair[0] in fx_currencies and pair[1] in fx_currencies
    pip_size = (0.01 if "JPY" in pair else 0.0001) if is_fx_pair else 0.01
    try:
        return StrategyRule(
            side=match.group("side").lower(), symbol=symbol,
            timeframe=match.group("timeframe").lower(), conditions=tuple(conditions),
            risk_pct=float(match.group("risk")), stop_loss_pips=float(match.group("stop")),
            take_profit_pips=float(match.group("target")), pip_size=pip_size,
        )
    except Exception as exc:
        raise StrategyParseError(f"Rule parameters failed validation: {exc}") from exc
