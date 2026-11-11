"""Fee and break-even calculations for Kalshi, Polymarket US and polymarket.com."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .rates import Rates, Rounding, default_rates

Platform = Literal["kalshi", "polymarket-us", "polymarket"]
Side = Literal["buy", "sell"]
Number = Decimal | int | float | str

PLATFORMS: tuple[Platform, ...] = ("kalshi", "polymarket-us", "polymarket")

_ALIASES: dict[str, Platform] = {
    "kalshi": "kalshi",
    "polymarket-us": "polymarket-us",
    "polymarket_us": "polymarket-us",
    "pm-us": "polymarket-us",
    "polymarket": "polymarket",
    "polymarket.com": "polymarket",
    "polymarket-global": "polymarket",
    "polymarket_global": "polymarket",
}

DISPLAY_NAMES: dict[Platform, str] = {
    "kalshi": "Kalshi",
    "polymarket-us": "Polymarket US",
    "polymarket": "polymarket.com",
}

ONE = Decimal(1)


def _num(value: Number, what: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{what} must be a number")
    # str() first so 0.1 (float) becomes Decimal("0.1"), not its binary expansion.
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite():
        raise ValueError(f"{what} must be finite")
    return d


def platform_key(platform: str) -> Platform:
    key = _ALIASES.get(platform.strip().lower())
    if key is None:
        raise ValueError(f"unknown platform {platform!r}; use one of: {', '.join(PLATFORMS)}")
    return key


@dataclass(frozen=True)
class FeeResult:
    """A fee in USD with everything that went into it."""

    platform: Platform
    platform_name: str
    side: Side
    role: Literal["taker", "maker"]
    price: Decimal
    contracts: Decimal
    coefficient: Decimal
    """The coefficient applied in coefficient × C × p × (1 − p), multipliers included."""
    raw_fee: Decimal
    """Exact fee before the platform's rounding."""
    fee: Decimal
    """Fee in USD after the platform's rounding (what you pay)."""
    rounding: str
    formula: str
    source_url: str
    rates_as_of: str
    details: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def usd(self) -> float:
        return float(self.fee)

    @property
    def trade_value(self) -> Decimal:
        """C × p: what a buyer pays, or a seller receives, before fees."""
        return self.contracts * self.price

    @property
    def fee_pct_of_trade_value(self) -> Decimal | None:
        """Fee as a percentage of the trade value at this price (None when the value is 0)."""
        value = self.trade_value
        return None if value == 0 else self.fee / value * 100

    @property
    def cash_flow(self) -> Decimal:
        """Signed cash for the trade including the fee: negative when you pay."""
        return -(self.trade_value + self.fee) if self.side == "buy" else self.trade_value - self.fee

    def breakdown(self) -> dict[str, str]:
        """Plain strings, ready for JSON."""
        pct = self.fee_pct_of_trade_value
        out = {
            "platform": self.platform_name,
            "side": self.side,
            "role": self.role,
            "price": str(self.price),
            "contracts": str(self.contracts),
            "trade_value_usd": str(self.trade_value),
            "coefficient": str(self.coefficient),
            "raw_fee_usd": str(self.raw_fee),
            "fee_usd": str(self.fee),
            "fee_pct_of_trade_value": "" if pct is None else str(pct.quantize(Decimal("0.0001"))),
            "cash_flow_usd": str(self.cash_flow),
            "rounding": self.rounding,
            "formula": self.formula,
            "source_url": self.source_url,
            "rates_as_of": self.rates_as_of,
        }
        out.update(self.details)
        return out


def _quadratic(coefficient: Decimal, contracts: Decimal, price: Decimal) -> Decimal:
    return coefficient * contracts * price * (ONE - price)


def _us_tier(rates: Rates, tier: int | str | None, volume_30d: Number | None) -> tuple[Decimal, str]:
    us = rates.polymarket_us
    if tier is not None and volume_30d is not None:
        raise ValueError("give either tier or volume_30d, not both")
    if volume_30d is not None:
        vol = _num(volume_30d, "volume_30d")
        if vol < 0:
            raise ValueError("volume_30d must be >= 0")
        for t in us.tiers:
            if t.below is None or vol < t.below:
                return t.theta, f"{t.label} (30-day taker volume ${vol:,})"
    if tier is None:
        first = us.tiers[0]
        return first.theta, f"{first.label} (default: the highest coefficient)"
    if isinstance(tier, str) and tier.strip().lower() in ("new", "new-account", "new_account"):
        return us.new_account_theta, f"New account ({us.new_account_note})"
    try:
        n = int(str(tier).strip().lower().removeprefix("tier").removeprefix("t").strip())
    except ValueError:
        n = 0
    for t in us.tiers:
        if t.number == n:
            return t.theta, t.label
    valid = ", ".join([str(t.number) for t in us.tiers] + ["new"])
    raise ValueError(f"unknown Polymarket US tier {tier!r}; use one of: {valid}")


def _global_category(rates: Rates, category: str | None) -> tuple[str, Decimal]:
    cats = rates.polymarket_global.rates
    if category is None:
        raise ValueError(f"polymarket.com needs a category; one of: {', '.join(cats)}")
    for name, rate in cats.items():
        if name.lower() == category.strip().lower():
            return name, rate
    raise ValueError(f"unknown polymarket.com category {category!r}; use one of: {', '.join(cats)}")


# Smallest order size step per platform (official docs):
# * kalshi: whole contracts.
# * polymarket-us: 0.01 contracts on most markets ("Fractional Contracts",
#   https://docs.polymarket.us/learn/trading/basics/fractional-shares.md); a few
#   markets, mostly futures, trade whole contracts only, which pmfees cannot see.
# * polymarket (polymarket.com): shares are six-decimal units, e.g. a market buy of
#   19.2307 shares (https://docs.polymarket.com/trading/place-orders.md).
CONTRACT_STEP: dict[str, Decimal] = {
    "kalshi": Decimal("1"),
    "polymarket-us": Decimal("0.01"),
    "polymarket": Decimal("0.000001"),
}
CONTRACT_STEP_TEXT: dict[str, str] = {
    "kalshi": "whole contracts only, e.g. 7",
    "polymarket-us": "steps of 0.01 contracts, e.g. 153.84",
    "polymarket": "up to 6 decimals, e.g. 19.2307",
}


def fee(
    platform: str,
    price: Number,
    contracts: Number,
    side: Side = "buy",
    tier: int | str | None = None,
    maker: bool = False,
    *,
    category: str | None = None,
    multiplier: Number | None = None,
    volume_30d: Number | None = None,
    rates: Rates | None = None,
) -> FeeResult:
    """Trading fee for one order.

    ``price`` is the price of the contract you trade, in dollars (0 < price < 1).
    ``side`` only changes the cash flow; the fee formulas are symmetric.

    Platform options:

    * ``kalshi``: ``multiplier`` is the multiplier M. It defaults to 1 for takers
      and 0 for makers (Kalshi charges makers only where the market says so, so pass
      ``multiplier=1`` with ``maker=True`` for those). The fee is rounded up so that
      fee + position cost (C × P) is a whole centicent ($0.0001).
    * ``polymarket-us``: ``tier`` is 1-4 or ``"new"`` (first 7 days), or give
      ``volume_30d`` (trailing 30-day taker volume in USD). Default: Tier 1.
      Makers pay no fee.
    * ``polymarket`` (polymarket.com): ``category`` is required. Makers pay no fee.
    """
    r = rates if rates is not None else default_rates()
    key = platform_key(platform)
    p = _num(price, "price")
    c = _num(contracts, "contracts")
    if not ONE > p > 0:
        raise ValueError("price must be between 0 and 1 (exclusive), in dollars")
    if c <= 0:
        raise ValueError("contracts must be > 0")
    step = CONTRACT_STEP[key]
    if c % step != 0:
        raise ValueError(f"contracts must be a multiple of {step} on {key} ({CONTRACT_STEP_TEXT[key]})")
    if side not in ("buy", "sell"):
        raise ValueError("side must be 'buy' or 'sell'")
    if key != "polymarket-us" and (tier is not None or volume_30d is not None):
        raise ValueError("tier and volume_30d only apply to polymarket-us")
    if key != "kalshi" and multiplier is not None:
        raise ValueError("multiplier only applies to kalshi")
    if key != "polymarket" and category is not None:
        raise ValueError("category only applies to polymarket (polymarket.com)")

    role: Literal["taker", "maker"] = "maker" if maker else "taker"
    details: dict[str, str] = {}
    notes: list[str] = []
    rounding: Rounding

    if key == "kalshi":
        k = r.kalshi
        default_m = Decimal(0) if maker else ONE
        m = default_m if multiplier is None else _num(multiplier, "multiplier")
        if m not in k.multipliers:
            allowed = ", ".join(str(x) for x in k.multipliers)
            raise ValueError(f"unknown Kalshi series multiplier {m}; the rates file lists: {allowed}")
        base = k.maker_rate if maker else k.taker_rate
        coefficient = m * base
        details["base_rate"] = str(base)
        details["multiplier"] = str(m)
        if k.version:
            details["fee_schedule_version"] = k.version
        if maker and multiplier is None:
            notes.append(
                "Kalshi maker multiplier defaults to 0; pass multiplier=1 for markets with maker fees."
            )
        rounding, formula, source, as_of = k.rounding, k.formula, k.source_url, k.as_of
    elif key == "polymarket-us":
        us = r.polymarket_us
        theta, label = _us_tier(r, tier, volume_30d)
        details["tier"] = label
        details["theta"] = str(theta)
        if maker:
            coefficient = Decimal(0)
            notes.append(f"Makers: {us.makers}. Maker rebates are not modelled.")
        else:
            coefficient = theta
        rounding, formula, source, as_of = us.rounding, us.formula, us.source_url, us.as_of
    else:
        g = r.polymarket_global
        cat, rate = _global_category(r, category)
        details["category"] = cat
        details["category_rate"] = str(rate)
        if maker:
            coefficient = Decimal(0)
            notes.append("polymarket.com: makers are never charged fees.")
        else:
            coefficient = rate
        notes.append(
            "Each market's own feeSchedule is authoritative; series can differ from the category rate."
        )
        rounding, formula, source, as_of = g.rounding, g.formula, g.source_url, g.as_of

    raw = _quadratic(coefficient, c, p)
    # Kalshi (basis fee_plus_position_cost) rounds fee + C × P; the others round the fee alone.
    rounded, rounding_text = rounding.apply(raw, c * p), rounding.describe()
    return FeeResult(
        platform=key,
        platform_name=DISPLAY_NAMES[key],
        side=side,
        role=role,
        price=p,
        contracts=c,
        coefficient=coefficient,
        raw_fee=raw,
        fee=rounded,
        rounding=rounding_text,
        formula=formula,
        source_url=source,
        rates_as_of=as_of or r.as_of,
        details=details,
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class BreakEven:
    """The smallest price move that puts a round trip in profit after the fees on both orders."""

    platform_name: str
    side: Side
    contracts: Decimal
    entry_price: Decimal
    entry_fee: Decimal
    exit_price: Decimal | None
    """First price on the tick grid where the round trip is > 0 after fees (None: none exists)."""
    exit_fee: Decimal | None
    move: Decimal | None
    """exit_price − entry_price (positive for a buy, negative for a sell)."""
    profit_at_exit: Decimal | None
    tick: Decimal

    @property
    def move_cents(self) -> Decimal | None:
        return None if self.move is None else self.move * 100


def break_even(
    platform: str,
    price: Number,
    contracts: Number,
    side: Side = "buy",
    tier: int | str | None = None,
    maker: bool = False,
    *,
    tick: Number = "0.01",
    category: str | None = None,
    multiplier: Number | None = None,
    volume_30d: Number | None = None,
    rates: Rates | None = None,
) -> BreakEven:
    """How far the price must move before closing the position is in profit after fees.

    Opens ``contracts`` at ``price`` (``side="buy"`` buys, ``"sell"`` sells) and closes
    them at a later price on the ``tick`` grid, paying the fee on both orders with the
    same role and options. Returns the first exit price where
    proceeds − cost − entry fee − exit fee > 0.
    """
    r = rates if rates is not None else default_rates()
    t = _num(tick, "tick")
    if t <= 0:
        raise ValueError("tick must be > 0")

    def leg(at: Number, leg_side: Side) -> FeeResult:
        return fee(
            platform,
            at,
            contracts,
            leg_side,
            tier,
            maker,
            category=category,
            multiplier=multiplier,
            volume_30d=volume_30d,
            rates=r,
        )

    entry = leg(price, side)
    exit_side: Side = "sell" if side == "buy" else "buy"
    step = t if side == "buy" else -t
    q = entry.price + step
    while ONE > q > 0:
        ex = leg(q, exit_side)
        profit = entry.cash_flow + ex.cash_flow
        if profit > 0:
            return BreakEven(
                platform_name=entry.platform_name,
                side=side,
                contracts=entry.contracts,
                entry_price=entry.price,
                entry_fee=entry.fee,
                exit_price=q,
                exit_fee=ex.fee,
                move=q - entry.price,
                profit_at_exit=profit,
                tick=t,
            )
        q += step
    return BreakEven(
        platform_name=entry.platform_name,
        side=side,
        contracts=entry.contracts,
        entry_price=entry.price,
        entry_fee=entry.fee,
        exit_price=None,
        exit_fee=None,
        move=None,
        profit_at_exit=None,
        tick=t,
    )
