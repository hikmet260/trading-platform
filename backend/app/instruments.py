from __future__ import annotations

"""Instrument metadata used for risk, P&L, and position sizing.

The platform stores position quantity in *venue units*: lots for MT5 FX/metals
and base-asset units for crypto.  A price move only becomes money after it is
multiplied by the contract size and converted from the instrument's quote
currency into the account currency.  Keeping that conversion in one place
prevents the historical bug where a 20-pip EUR/USD move was treated as $0.002
instead of $200 per standard lot.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from functools import lru_cache
from typing import Literal

AssetClass = Literal["fx", "metal", "crypto"]

FX_CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
    "SEK", "NOK", "DKK", "SGD", "HKD", "MXN", "ZAR", "TRY", "PLN", "CZK", "HUF",
}
STABLE_QUOTES = {
    "USDT": "USD", "USDC": "USD", "BUSD": "USD", "FDUSD": "USD",
    "TUSD": "USD", "DAI": "USD", "USD1": "USD",
}
METALS = {
    # pip size, contract size (troy oz per lot), min, step, max, leverage
    "XAU": (0.10, 100.0, 0.01, 0.01, 50.0, 20.0, 2),
    "XAG": (0.01, 5_000.0, 0.01, 0.01, 50.0, 20.0, 3),
    "XPT": (0.01, 100.0, 0.01, 0.01, 50.0, 10.0, 2),
    "XPD": (0.01, 100.0, 0.01, 0.01, 50.0, 10.0, 2),
}
CRYPTO_OVERRIDES = {
    # base: pip, min, step, max, leverage, price decimals
    "BTC": (1.0, 0.0001, 0.0001, 1_000.0, 5.0, 2),
    "ETH": (0.1, 0.001, 0.001, 10_000.0, 5.0, 2),
    "SOL": (0.01, 0.01, 0.01, 100_000.0, 5.0, 3),
    "XRP": (0.0001, 1.0, 1.0, 10_000_000.0, 5.0, 4),
    "DOGE": (0.00001, 10.0, 1.0, 100_000_000.0, 5.0, 5),
    "ADA": (0.0001, 1.0, 1.0, 10_000_000.0, 5.0, 4),
    "BNB": (0.01, 0.001, 0.001, 100_000.0, 5.0, 2),
}


class InstrumentError(ValueError):
    """Raised when a symbol cannot be safely mapped to contract metadata."""


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    normalized: str
    asset_class: AssetClass
    base_currency: str
    quote_currency: str
    pip_size: float
    contract_size: float
    min_quantity: float
    quantity_step: float
    max_quantity: float
    price_decimals: int
    leverage_hint: float

    @property
    def quantity_unit(self) -> str:
        return "lots" if self.asset_class in {"fx", "metal"} else "units"

    @property
    def settlement_currency(self) -> str:
        return STABLE_QUOTES.get(self.quote_currency, self.quote_currency)


def _split_symbol(raw: str) -> tuple[str, str]:
    value = raw.strip().upper().replace(" ", "")
    for separator in ("/", ":", "-", "_"):
        if separator in value:
            parts = [part for part in value.split(separator) if part]
            if len(parts) == 2 and all(2 <= len(part) <= 10 for part in parts):
                return parts[0], parts[1]
            raise InstrumentError(f"Unsupported symbol format: {raw}")
    if len(value) == 6 and value[:3] in FX_CURRENCIES and value[3:] in FX_CURRENCIES:
        return value[:3], value[3:]
    for metal in METALS:
        if value.startswith(metal) and len(value) == 6:
            return value[:3], value[3:]
    # Crypto quotes are deliberately checked longest-first so BTCUSDT does not
    # get parsed as BTCU/SDT or similar.
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD", "BTC", "ETH", "EUR"):
        if value.endswith(quote) and len(value) > len(quote) + 1:
            return value[: -len(quote)], quote
    raise InstrumentError(
        f"Cannot determine base/quote currencies for '{raw}'. Use e.g. EUR/USD, XAUUSD, or BTC/USDT."
    )


@lru_cache(maxsize=512)
def resolve_instrument(symbol: str) -> InstrumentSpec:
    base, quote = _split_symbol(symbol)
    normalized = f"{base}{quote}"
    if base in METALS:
        pip, contract, minimum, step, maximum, leverage, decimals = METALS[base]
        return InstrumentSpec(
            symbol=symbol.strip().upper(), normalized=normalized, asset_class="metal",
            base_currency=base, quote_currency=quote, pip_size=pip,
            contract_size=contract, min_quantity=minimum, quantity_step=step,
            max_quantity=maximum, price_decimals=decimals, leverage_hint=leverage,
        )
    if base in FX_CURRENCIES and quote in FX_CURRENCIES:
        pip = 0.01 if quote == "JPY" else 0.0001
        return InstrumentSpec(
            symbol=symbol.strip().upper(), normalized=normalized, asset_class="fx",
            base_currency=base, quote_currency=quote, pip_size=pip,
            contract_size=100_000.0, min_quantity=0.01, quantity_step=0.01,
            max_quantity=100.0, price_decimals=3 if quote == "JPY" else 5, leverage_hint=30.0,
        )
    if base in FX_CURRENCIES or quote in FX_CURRENCIES or quote in STABLE_QUOTES or quote in {"BTC", "ETH"}:
        pip, minimum, step, maximum, leverage, decimals = CRYPTO_OVERRIDES.get(
            base, (0.01, 0.01, 0.01, 1_000_000.0, 3.0, 3),
        )
        return InstrumentSpec(
            symbol=symbol.strip().upper(), normalized=normalized, asset_class="crypto",
            base_currency=base, quote_currency=quote, pip_size=pip,
            contract_size=1.0, min_quantity=minimum, quantity_step=step,
            max_quantity=maximum, price_decimals=decimals, leverage_hint=leverage,
        )
    raise InstrumentError(f"Unsupported instrument '{symbol}'")


def quote_to_account_rate(
    spec: InstrumentSpec, price: float, account_currency: str = "USD",
) -> float:
    """Return how many account-currency units one quote-currency unit is worth.

    FX P&L is denominated in the quote currency.  USD-quoted pairs therefore
    need no conversion; for USD/JPY the P&L is JPY and must be divided by the
    USDJPY rate.  Cross pairs without an explicit conversion path are rejected
    rather than silently valued at 1.0.
    """
    if price <= 0 or price != price:
        raise InstrumentError("A positive finite price is required for currency conversion")
    account = account_currency.strip().upper()
    quote = spec.quote_currency.upper()
    if quote == account:
        return 1.0
    if STABLE_QUOTES.get(quote) == account:
        return 1.0
    if spec.base_currency.upper() == account:
        return 1.0 / price
    raise InstrumentError(
        f"No conversion path from {quote} to account currency {account} for {spec.symbol}. "
        "Use a pair quoted in the account currency or configure an explicit conversion."
    )


def round_quantity(spec: InstrumentSpec, quantity: float, *, rounding: Literal["down", "up", "nearest"] = "down") -> float:
    if quantity <= 0 or quantity != quantity:
        return 0.0
    step = Decimal(str(spec.quantity_step))
    value = Decimal(str(quantity))
    # Floats such as 0.0020000000000000018 must not turn 0.25 lots into
    # 0.24 when flooring.  The epsilon is far below one quantity step.
    epsilon = step * Decimal("0.000001")
    if rounding == "down":
        units = ((value + epsilon) / step).to_integral_value(rounding=ROUND_DOWN)
    elif rounding == "up":
        units = (value / step).to_integral_value(rounding=ROUND_UP)
    else:
        units = (value / step).to_integral_value()
    return float(units * step)


def clamp_quantity(spec: InstrumentSpec, quantity: float) -> float:
    rounded = round_quantity(spec, quantity, rounding="down")
    return min(max(rounded, spec.min_quantity), spec.max_quantity)


def risk_per_quantity(
    spec: InstrumentSpec, stop_distance: float, price: float, account_currency: str = "USD",
) -> float:
    """Account-currency loss for one venue unit at the supplied stop distance."""
    if stop_distance <= 0:
        return 0.0
    rate = quote_to_account_rate(spec, price, account_currency)
    return stop_distance * spec.contract_size * rate


def quantity_for_risk(
    spec: InstrumentSpec, risk_amount: float, stop_distance: float, price: float,
    account_currency: str = "USD",
) -> float:
    per_unit = risk_per_quantity(spec, stop_distance, price, account_currency)
    if per_unit <= 0 or risk_amount <= 0:
        return 0.0
    return round_quantity(spec, risk_amount / per_unit, rounding="down")


def notional_in_account(
    spec: InstrumentSpec, quantity: float, price: float, account_currency: str = "USD",
) -> float:
    rate = quote_to_account_rate(spec, price, account_currency)
    return abs(quantity) * spec.contract_size * price * rate


def pnl_in_account(
    spec: InstrumentSpec, quantity: float, entry_price: float, exit_price: float,
    side: str, account_currency: str = "USD",
) -> float:
    direction = 1.0 if side.lower() == "buy" else -1.0
    conversion_price = exit_price if exit_price > 0 else entry_price
    rate = quote_to_account_rate(spec, conversion_price, account_currency)
    return quantity * spec.contract_size * (exit_price - entry_price) * direction * rate
