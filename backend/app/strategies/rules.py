from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.instruments import InstrumentError, resolve_instrument


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

    side: Literal["buy", "sell", "both"]
    symbol: str = Field(min_length=3, max_length=40, pattern=r"^[A-Z0-9/:_-]+$")
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    conditions: tuple[RuleCondition, ...] = Field(min_length=1, max_length=5)
    risk_pct: float = Field(default=0.5, gt=0, le=1.0, description="Percent of equity at risk per paper trade")
    stop_loss_pips: float = Field(gt=0, le=10_000)
    take_profit_pips: float = Field(gt=0, le=50_000)
    pip_size: float
    sizing_mode: Literal["risk_pct", "fixed_lots"] = "risk_pct"
    base_lot_size: float | None = Field(default=None, gt=0, le=100.0)
    tier_lots: tuple[float, ...] | None = Field(default=None, min_length=1, max_length=10)
    short_conditions: tuple[RuleCondition, ...] | None = Field(
        default=None, min_length=1, max_length=5,
        description="Explicit short-side conditions for side='both'; auto-mirrored when omitted.",
    )

    @model_validator(mode="after")
    def normalize_both_direction(self) -> "StrategyRule":
        if self.side == "both" and self.short_conditions is None:
            object.__setattr__(self, "short_conditions", mirror_conditions(self.conditions))
        if self.side != "both" and self.short_conditions is not None:
            raise ValueError("short_conditions are only valid when side is 'both'")
        return self


class StrategyParseError(ValueError):
    pass


_SIZING_PATTERN = (
    r"(?:risk\s+\d+(?:\.\d+)?\s*%|"
    r"(?:(?:the\s+)?same\s+|sizing\s+fixed\s+)?(?:lot\s+size|base\s+lot|base|lots?)\b.+?)"
)
_PROMPT_STANDARD = re.compile(
    r"^(?P<side>buy\s*(?:&|and)\s*sell|both|buy|sell)\s+(?P<symbol>[A-Z0-9/:_-]+)\s+on\s+"
    r"(?P<timeframe>1m|5m|15m|30m|1h|4h|1d)\s+when\s+(?P<conditions>.+?)\s+"
    rf"(?P<sizing>{_SIZING_PATTERN})\s+"
    r"(?:stop\s*loss|sl)\s+(?P<stop>\d+(?:\.\d+)?)\s*pips?\s+(?:take\s*profit|tp)\s+(?P<target>\d+(?:\.\d+)?)\s*pips?\.?$",
    re.IGNORECASE,
)
_PROMPT_SIZING_FIRST = re.compile(
    r"^(?P<side>buy\s*(?:&|and)\s*sell|both|buy|sell)\s+"
    rf"(?P<sizing>{_SIZING_PATTERN})\s+"
    r"(?:on\s+|for\s+)?(?P<symbol>[A-Z0-9/:_-]+)\s+(?:on\s+)?(?P<timeframe>1m|5m|15m|30m|1h|4h|1d)\s+when\s+(?P<conditions>.+?)\s+"
    r"(?:stop\s*loss|sl)\s+(?P<stop>\d+(?:\.\d+)?)\s*pips?\s+(?:take\s*profit|tp)\s+(?P<target>\d+(?:\.\d+)?)\s*pips?\.?$",
    re.IGNORECASE,
)
_THRESHOLD = re.compile(r"^(RSI|EMA|SMA|PRICE|CLOSE)(?:\((\d+)\))?\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)$", re.I)
_CROSS = re.compile(
    r"^(EMA|SMA)\((\d+)\)\s+crosses[\s_]+(above|below)\s+(EMA|SMA)\((\d+)\)$", re.I,
)
_FLIPPED_OPERATOR = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}
_FLIPPED_CROSS = {"crosses_above": "crosses_below", "crosses_below": "crosses_above"}


def mirror_conditions(conditions: tuple[RuleCondition, ...]) -> tuple[RuleCondition, ...]:
    """Build a deterministic opposite-side rule.

    RSI thresholds are mirrored around 50 (RSI < 30 -> RSI > 70) because
    inverting the operator alone would make a short strategy fire almost all
    the time.  Other thresholds and moving-average crosses use the opposite
    comparison.  This makes ``side='both'`` explicit and reproducible instead
    of guessing a direction from the first condition.
    """
    mirrored: list[RuleCondition] = []
    for condition in conditions:
        if condition.kind == "threshold":
            value = condition.value
            if value is None:
                raise ValueError("Threshold condition is missing a value")
            if condition.indicator == "rsi":
                value = 100.0 - value
            mirrored.append(condition.model_copy(update={
                "operator": _FLIPPED_OPERATOR[condition.operator],
                "value": value,
            }))
        else:
            mirrored.append(condition.model_copy(update={
                "operator": _FLIPPED_CROSS[condition.operator],
            }))
    return tuple(mirrored)


def _parse_sizing(sizing_text: str) -> dict:
    normalized = " ".join(sizing_text.strip().split())
    risk_match = re.fullmatch(r"risk\s+(\d+(?:\.\d+)?)\s*%", normalized, re.I)
    if risk_match:
        risk_pct = float(risk_match.group(1))
        if not (0 < risk_pct <= 1.0):
            raise StrategyParseError("Risk percent must be between 0 and 1.0% (e.g. risk 0.5%)")
        return {
            "sizing_mode": "risk_pct",
            "risk_pct": risk_pct,
            "base_lot_size": None,
            "tier_lots": None,
        }

    base_match = re.search(r"(?:lot\s+size|base\s+lot|base|lots?)\s+(\d+(?:\.\d+)?)", normalized, re.I)
    if not base_match:
        raise StrategyParseError(
            f"Unsupported sizing rule '{sizing_text}'. Use 'risk 0.5%' or "
            "'lot size 0.12 tiers 0.36, 0.72' (or position 1 0.36 position 2 0.72)"
        )
    base_lot = float(base_match.group(1))
    if base_lot <= 0 or base_lot > 100.0:
        raise StrategyParseError("Base lot size must be between 0.01 and 100.0")

    def _extract_val(expr: str) -> float:
        clean = expr.strip()
        if "=" in clean:
            clean = clean.split("=")[-1].strip()
        if "x" in clean.lower() or "*" in clean:
            parts = re.split(r"[xX*]", clean)
            return round(float(parts[0].strip()) * float(parts[1].strip()), 4)
        return round(float(clean), 4)

    p1_match = re.search(
        r"(?:1st|first|position\s+1)\s*(?:position\s*)?(?:also\s*)?(?:=\s*)?(\d+(?:\.\d+)?(?:\s*[xX*]\s*\d+(?:\.\d+)?)?(?:\s*=\s*\d+(?:\.\d+)?)?)",
        normalized, re.I,
    )
    p2_match = re.search(
        r"(?:2nd|second|position\s+2)\s*(?:position\s*)?(?:one\s*)?(?:also\s*)?(?:=\s*)?(\d+(?:\.\d+)?(?:\s*[xX*]\s*\d+(?:\.\d+)?)?(?:\s*=\s*\d+(?:\.\d+)?)?)",
        normalized, re.I,
    )

    if p1_match and p2_match:
        tier_lots = (_extract_val(p1_match.group(1)), _extract_val(p2_match.group(1)))
    else:
        tiers_match = re.search(r"tiers?[\s:]*([0-9.,\s]+)", normalized, re.I)
        if tiers_match:
            raw = [x.strip() for x in tiers_match.group(1).replace(",", " ").split() if x.strip()]
            tier_lots = tuple(round(float(x), 4) for x in raw)
        else:
            if base_lot == 0.12:
                tier_lots = (0.36, 0.72)
            else:
                tier_lots = (round(base_lot * 3, 4), round(base_lot * 6, 4))

    for tier in tier_lots:
        if tier <= 0 or tier > 100.0:
            raise StrategyParseError("Tier lot size must be between 0.01 and 100.0")

    return {
        "sizing_mode": "fixed_lots",
        "risk_pct": 0.5,
        "base_lot_size": base_lot,
        "tier_lots": tier_lots,
    }


def parse_strategy_prompt(prompt: str) -> StrategyRule:
    """Parse the supported English strategy grammar without an LLM or fuzzy mapping."""
    normalized = " ".join(prompt.strip().split())
    match = _PROMPT_STANDARD.fullmatch(normalized) or _PROMPT_SIZING_FIRST.fullmatch(normalized)
    if not match:
        raise StrategyParseError(
            "Unsupported prompt. Use: BUY EUR/USD on 15m when RSI(14) < 30 AND "
            "EMA(9) crosses above EMA(21) risk 0.5% stop loss 20 pips take profit 40 pips "
            "OR: Buy & Sell EUR/USD on 15m when RSI(14) < 30 lot size 0.12 tiers 0.36, 0.72 "
            "stop loss 20 pips take profit 40 pips"
        )

    conditions: list[RuleCondition] = []
    for clause in re.split(r"\s+and\s+", match.group("conditions"), flags=re.I):
        threshold = _THRESHOLD.fullmatch(clause.strip())
        cross = _CROSS.fullmatch(clause.strip())
        if threshold:
            name, period, operator, value = threshold.groups()
            indicator = {"price": "close", "close": "close"}.get(name.lower(), name.lower())
            if indicator == "rsi" and period is None:
                parsed_period: int | None = 14
            elif indicator != "close" and period is None:
                raise StrategyParseError(f"{name.upper()} requires a period, for example {name.upper()}(14)")
            else:
                parsed_period = int(period) if period else None
            if indicator == "close" and period is not None:
                raise StrategyParseError("PRICE/CLOSE does not take a period")
            conditions.append(RuleCondition(
                kind="threshold", indicator=indicator, period=parsed_period,
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
    try:
        spec = resolve_instrument(symbol)
    except InstrumentError as exc:
        raise StrategyParseError(str(exc)) from exc
    pip_size = spec.pip_size

    raw_side = match.group("side").strip().lower()
    if ("buy" in raw_side and "sell" in raw_side) or raw_side == "both":
        side = "both"
    elif "buy" in raw_side:
        side = "buy"
    else:
        side = "sell"

    sizing_info = _parse_sizing(match.group("sizing"))
    try:
        return StrategyRule(
            side=side, symbol=symbol,
            timeframe=match.group("timeframe").lower(), conditions=tuple(conditions),
            risk_pct=sizing_info["risk_pct"], stop_loss_pips=float(match.group("stop")),
            take_profit_pips=float(match.group("target")), pip_size=pip_size,
            sizing_mode=sizing_info["sizing_mode"],
            base_lot_size=sizing_info["base_lot_size"],
            tier_lots=sizing_info["tier_lots"],
        )
    except Exception as exc:
        raise StrategyParseError(f"Rule parameters failed validation: {exc}") from exc
