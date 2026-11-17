"""Fee and break-even calculations for Kalshi, Polymarket US and polymarket.com."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Literal

from .kalshi_series import KalshiSeries, default_kalshi_series
from .rates import GlobalPeriod, Rates, RebateTier, Rounding, default_rates

Platform = Literal["kalshi", "polymarket-us", "polymarket"]
Side = Literal["buy", "sell"]
Number = Decimal | int | float | str
When = datetime | str
"""A fill time: a timezone-aware ``datetime`` or an ISO 8601 string with an offset (``2026-11-21T08:00Z``)."""

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(at: When | None) -> datetime:
    """``at`` as an aware UTC datetime; ``None`` is now."""
    if at is None:
        return _now()
    if isinstance(at, str):
        text = at.strip()
        try:
            at = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in ("Z", "z") else text)
        except ValueError as exc:
            raise ValueError(f"at must be an ISO 8601 time, e.g. 2026-11-21T08:00:00Z; got {text!r}") from exc
    if not isinstance(at, datetime):
        raise TypeError("at must be a datetime or an ISO 8601 string")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at needs a timezone (e.g. 2026-11-21T08:00:00Z); naive times are ambiguous")
    return at.astimezone(timezone.utc)


def _utc_text(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H:%M UTC")


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


def _global_category(period: GlobalPeriod, category: str | None) -> tuple[str, Decimal, str | None]:
    """(category, rate, note). A subcategory such as ``Politics (elections)`` falls back to its parent
    category in a period that does not list it (before 2026-11-20 there was one Politics rate)."""
    cats = period.rates
    if category is None:
        raise ValueError(f"polymarket.com needs a category; one of: {', '.join(cats)}")
    wanted = category.strip().lower()
    for name, rate in cats.items():
        if name.lower() == wanted:
            return name, rate, None
    parent = wanted.split("(", 1)[0].strip()
    if parent != wanted:
        for name, rate in cats.items():
            if name.lower() == parent:
                return (
                    name,
                    rate,
                    f"{category.strip()} uses the {name} rate in this period (no separate rate).",
                )
    raise ValueError(f"unknown polymarket.com category {category!r}; use one of: {', '.join(cats)}")


def _period_label(rates: Rates, period: GlobalPeriod) -> str:
    if period.effective is not None:
        return f"from {_utc_text(period.effective)}"
    first = rates.polymarket_global.schedule[0].effective if rates.polymarket_global.schedule else None
    return f"before {_utc_text(first)}" if first is not None else "current"


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
    series: str | KalshiSeries | None = None,
    volume_30d: Number | None = None,
    at: When | None = None,
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
      ``series`` is a series, event or market ticker (``"KXMLBGAME"``) or a
      ``KalshiSeries``: M and whether makers pay come from Kalshi's per-series
      ``fee_type`` and ``fee_multiplier`` (``kalshi_series.json``). Give either
      ``series`` or ``multiplier``.
    * ``polymarket-us``: ``tier`` is 1-4 or ``"new"`` (first 7 days), or give
      ``volume_30d`` (trailing 30-day taker volume in USD). Default: Tier 1.
      Makers pay no fee.
    * ``polymarket`` (polymarket.com): ``category`` is required. Makers pay no fee.
      ``at`` is the fill time (default: now). The rates file can hold dated changes
      (``schedule``): a fill before a change's effective time uses the older rates, a fill
      at or after it the newer ones (from 2026-11-20 23:59 UTC: Crypto 0.01, Politics 0.01,
      ``Politics (elections)`` and ``Crypto (BTC 5m/15m up/down)`` 0.05, ...).
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
    if key != "kalshi" and series is not None:
        raise ValueError("series only applies to kalshi")
    if key != "polymarket" and category is not None:
        raise ValueError("category only applies to polymarket (polymarket.com)")
    if key != "polymarket" and at is not None:
        raise ValueError(
            "at only applies to polymarket (polymarket.com); the other platforms have one rate set"
        )

    role: Literal["taker", "maker"] = "maker" if maker else "taker"
    details: dict[str, str] = {}
    notes: list[str] = []
    rounding: Rounding

    if key == "kalshi":
        k = r.kalshi
        s: KalshiSeries | None = None
        if series is not None:
            if multiplier is not None:
                raise ValueError("give either series or multiplier, not both")
            table = default_kalshi_series()
            s = series if isinstance(series, KalshiSeries) else table.lookup(series)
            m = s.multiplier
            # Kalshi OpenAPI: maker fee = the taker formula × 0.25 (0.5 for combo maker fees),
            # × M; 0.07 × 0.25 is the fee schedule's maker rate 0.0175.
            base = k.taker_rate * s.maker_share if maker else k.taker_rate
            details["series"] = s.ticker
            details["series_fee_type"] = s.fee_type if s.listed else f"{s.fee_type} (not listed: standard)"
            details["series_as_of"] = table.as_of
            if not s.listed:
                notes.append(
                    f"{s.ticker} is not among the {len(table.series)} non-standard series in "
                    f"kalshi_series.json ({table.as_of}), so the standard structure "
                    f"({s.fee_type}, M = {s.multiplier}) is assumed."
                )
            if maker and s.maker_share == 0:
                notes.append(f"Series {s.ticker} has no maker fees (fee_type {s.fee_type}).")
        else:
            default_m = Decimal(0) if maker else ONE
            m = default_m if multiplier is None else _num(multiplier, "multiplier")
            base = k.maker_rate if maker else k.taker_rate
        if m not in k.multipliers:
            allowed = ", ".join(str(x) for x in k.multipliers)
            raise ValueError(f"unknown Kalshi series multiplier {m}; the rates file lists: {allowed}")
        coefficient = m * base
        details["base_rate"] = str(base)
        details["multiplier"] = str(m)
        if k.version:
            details["fee_schedule_version"] = k.version
        if maker and multiplier is None and s is None:
            notes.append(
                "Kalshi maker multiplier defaults to 0; pass multiplier=1 for markets with maker fees, "
                "or series= to look it up."
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
        when = _when(at)
        period = g.period_at(when)
        cat, rate, cat_note = _global_category(period, category)
        details["category"] = cat
        details["category_rate"] = str(rate)
        details["fill_time"] = when.isoformat().replace("+00:00", "Z")
        details["rate_period"] = _period_label(r, period)
        if cat_note:
            notes.append(cat_note)
        if maker:
            coefficient = Decimal(0)
            notes.append("polymarket.com: makers are never charged fees.")
        else:
            coefficient = rate
        notes.append(
            "Each market's own feeSchedule is authoritative; series can differ from the category rate."
        )
        rounding, formula, source, as_of = g.rounding, g.formula, period.source_url, period.as_of or g.as_of

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
    series: str | KalshiSeries | None = None,
    volume_30d: Number | None = None,
    at: When | None = None,
    rates: Rates | None = None,
) -> BreakEven:
    """How far the price must move before closing the position is in profit after fees.

    Opens ``contracts`` at ``price`` (``side="buy"`` buys, ``"sell"`` sells) and closes
    them at a later price on the ``tick`` grid, paying the fee on both orders with the
    same role and options. Returns the first exit price where
    proceeds − cost − entry fee − exit fee > 0. On polymarket.com both orders use the rates
    in force at ``at`` (default: now).
    """
    r = rates if rates is not None else default_rates()
    if at is None and platform_key(platform) == "polymarket":
        at = _now()
    t = _num(tick, "tick")
    if t <= 0:
        raise ValueError("tick must be > 0")

    def leg(leg_price: Number, leg_side: Side) -> FeeResult:
        return fee(
            platform,
            leg_price,
            contracts,
            leg_side,
            tier,
            maker,
            category=category,
            multiplier=multiplier,
            series=series,
            volume_30d=volume_30d,
            at=at,
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


FillInput = tuple[Number, Number] | Mapping[str, object]
"""A fill: ``(price, contracts)`` or a mapping with ``price``, ``contracts`` and optionally ``maker``
and ``at`` (fill time, polymarket.com only)."""

SIX_DP = Decimal("0.000001")


@dataclass(frozen=True)
class FillFees:
    """Fees for a list of fills, each fill priced and rounded on its own, then summed."""

    platform_name: str
    side: Side
    fills: tuple[FeeResult, ...]
    """One ``FeeResult`` per fill; ``FeeResult.fee`` is that fill rounded on its own."""
    rebates: tuple[Decimal, ...]
    """Rounding refunded on each fill by Kalshi's per-order fee accumulator (all 0 unless same_order)."""
    same_order: bool
    """True when Kalshi's per-order fee accumulator was applied (only ever on Kalshi)."""
    unrebated_rounding: Decimal
    """Rounding still held in the accumulator after the last fill (0 unless same_order)."""

    @property
    def fees(self) -> tuple[Decimal, ...]:
        """Net fee per fill: the fill's rounded fee minus its rebate."""
        return tuple(f.fee - r for f, r in zip(self.fills, self.rebates, strict=True))

    @property
    def fee(self) -> Decimal:
        """Total fee in USD for all fills."""
        return sum(self.fees, Decimal(0))

    @property
    def raw_fee(self) -> Decimal:
        """Total of the fills' exact fees before rounding."""
        return sum((f.raw_fee for f in self.fills), Decimal(0))

    @property
    def contracts(self) -> Decimal:
        return sum((f.contracts for f in self.fills), Decimal(0))

    @property
    def trade_value(self) -> Decimal:
        return sum((f.trade_value for f in self.fills), Decimal(0))

    @property
    def cash_flow(self) -> Decimal:
        """Signed cash for all fills including the fees: negative when you pay."""
        sign = -1 if self.side == "buy" else 1
        return sign * self.trade_value - self.fee

    def breakdown(self) -> dict[str, object]:
        """Plain strings and lists, ready for JSON."""
        return {
            "platform": self.platform_name,
            "side": self.side,
            "same_order": self.same_order,
            "fills": [
                {
                    "price": str(f.price),
                    "contracts": str(f.contracts),
                    "role": f.role,
                    "raw_fee_usd": str(f.raw_fee),
                    "fee_usd": str(f.fee),
                    "rebate_usd": str(r),
                    "net_fee_usd": str(f.fee - r),
                }
                for f, r in zip(self.fills, self.rebates, strict=True)
            ],
            "contracts": str(self.contracts),
            "trade_value_usd": str(self.trade_value),
            "raw_fee_usd": str(self.raw_fee),
            "fee_usd": str(self.fee),
            "unrebated_rounding_usd": str(self.unrebated_rounding),
            "cash_flow_usd": str(self.cash_flow),
        }


def _fill(item: FillInput, i: int) -> tuple[Number, Number, bool | None, When | None]:
    if isinstance(item, Mapping):
        try:
            price, contracts = item["price"], item["contracts"]
        except KeyError as exc:
            raise ValueError(f"fill {i}: needs 'price' and 'contracts'") from exc
        maker = item.get("maker")
        if maker is not None and not isinstance(maker, bool):
            raise ValueError(f"fill {i}: 'maker' must be True or False")
        return price, contracts, maker, item.get("at")  # type: ignore[return-value]
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return item[0], item[1], None, None
    raise ValueError(f"fill {i}: expected (price, contracts) or a mapping with price and contracts")


def fill_fees(
    platform: str,
    fills: Iterable[FillInput],
    side: Side = "buy",
    tier: int | str | None = None,
    maker: bool = False,
    *,
    same_order: bool = True,
    category: str | None = None,
    multiplier: Number | None = None,
    series: str | KalshiSeries | None = None,
    volume_30d: Number | None = None,
    at: When | None = None,
    rates: Rates | None = None,
) -> FillFees:
    """Fee per fill, summed: each fill is priced and rounded on its own with ``fee()``.

    ``fills`` are ``(price, contracts)`` pairs, or mappings with ``price``, ``contracts`` and
    optionally ``maker`` (overrides ``maker`` for that fill). All other options are the same as
    for ``fee()`` and apply to every fill.

    Kalshi (default ``same_order=True``): the fills are one order. Each fill is rounded up to a
    whole centicent, and Kalshi's fee accumulator refunds that rounding in centicent steps, so
    the total converges to one equivalent fill. Kalshi: "The fee accumulator applies across all
    fills of an order so that the total fee converges to what a single equivalent fill would
    cost" (https://docs.kalshi.com/getting_started/fee_rounding.md). Pass ``same_order=False``
    for fills from different orders: each is rounded on its own and nothing is refunded.

    Polymarket US and polymarket.com document no such refund, so there every fill is always
    rounded on its own and ``same_order`` has no effect.

    polymarket.com: each fill uses the rates in force at its own ``at`` (from the mapping), else
    the ``at`` argument, else now. Fills of one order on both sides of a dated change are
    therefore priced with the old and the new rates respectively.
    """
    r = rates if rates is not None else default_rates()
    key = platform_key(platform)
    accumulate = same_order and key == "kalshi"
    if at is not None and key != "polymarket":
        raise ValueError(
            "at only applies to polymarket (polymarket.com); the other platforms have one rate set"
        )
    results: list[FeeResult] = []
    for i, item in enumerate(fills):
        price, contracts, fill_maker, fill_at = _fill(item, i)
        if fill_at is not None and key != "polymarket":
            raise ValueError(f"fill {i}: 'at' only applies to polymarket (polymarket.com)")
        if key == "polymarket" and fill_at is None:
            fill_at = at if at is not None else _now()
        try:
            results.append(
                fee(
                    key,
                    price,
                    contracts,
                    side,
                    tier,
                    maker if fill_maker is None else fill_maker,
                    category=category,
                    multiplier=multiplier,
                    series=series,
                    volume_30d=volume_30d,
                    at=fill_at,
                    rates=r,
                )
            )
        except ValueError as exc:
            raise ValueError(f"fill {i}: {exc}") from exc
    if not results:
        raise ValueError("fills must not be empty")

    rebates = [Decimal(0)] * len(results)
    held = Decimal(0)
    if accumulate:
        # Kalshi fee rounding: trade fee = model fee rounded up to $0.000001; the rounding fee is
        # what the fill's balance alignment adds on top. It goes into the order's accumulator,
        # which is refunded in steps of the balance precision, never more than the fill's fee.
        step = Decimal(1).scaleb(-r.kalshi.rounding.decimals)
        for i, res in enumerate(results):
            trade_fee = res.raw_fee.quantize(SIX_DP, rounding=ROUND_UP)
            held += max(res.fee - trade_fee, Decimal(0))
            rebate = min(held, res.fee).quantize(step, rounding=ROUND_DOWN)
            rebates[i] = rebate
            held -= rebate
    return FillFees(
        platform_name=results[0].platform_name,
        side=side,
        fills=tuple(results),
        rebates=tuple(rebates),
        same_order=accumulate,
        unrebated_rounding=held,
    )


def weighted_volume(fills: Iterable[FillInput]) -> Decimal:
    """polymarket.com weighted volume: the sum of shares × price × (1 − price) over ``fills``.

    This is what the rebate tiers from 2026-11-20 23:59 UTC are measured in (over 30 days),
    not dollar volume (shares × price). Sum maker and taker fills separately: maker fills
    count only toward the maker tier, taker fills only toward the taker tier.
    """
    total = Decimal(0)
    for i, item in enumerate(fills):
        price, contracts, _maker, _at = _fill(item, i)
        p, c = _num(price, f"fill {i}: price"), _num(contracts, f"fill {i}: contracts")
        if not ONE > p > 0:
            raise ValueError(f"fill {i}: price must be between 0 and 1 (exclusive), in dollars")
        if c <= 0:
            raise ValueError(f"fill {i}: contracts must be > 0")
        total += c * p * (ONE - p)
    return total


Role = Literal["maker", "taker"]


def _rebate_period(role: str, at: When | None, rates: Rates) -> tuple[GlobalPeriod, tuple[RebateTier, ...]]:
    if role not in ("maker", "taker"):
        raise ValueError("role must be 'maker' or 'taker'")
    when = _when(at)
    period = rates.polymarket_global.period_at(when)
    tiers = period.maker_rebate_tiers if role == "maker" else period.taker_rebate_tiers
    if not tiers:
        later = [p.effective for p in rates.polymarket_global.schedule if p.effective and p.effective > when]
        since = f" from {_utc_text(later[0])}" if later else ""
        raise ValueError(
            f"polymarket.com {role} rebate tiers are only modelled{since}; at {_utc_text(when)} "
            "Polymarket ran the older programs (category multipliers, daily maker rebate pools)"
        )
    return period, tiers


def polymarket_rebate_tier(
    role: Role,
    weighted_volume_30d: Number,
    *,
    at: When | None = None,
    rates: Rates | None = None,
) -> RebateTier:
    """The polymarket.com rebate tier for a 30-day weighted volume (see ``weighted_volume()``)."""
    r = rates if rates is not None else default_rates()
    _period, tiers = _rebate_period(role, at, r)
    vol = _num(weighted_volume_30d, "weighted_volume_30d")
    if vol < 0:
        raise ValueError("weighted_volume_30d must be >= 0")
    for t in tiers:
        if t.below is None or vol < t.below:
            return t
    return tiers[-1]


@dataclass(frozen=True)
class Rebate:
    """A polymarket.com maker or taker rebate on taker fees."""

    role: Role
    tier: RebateTier
    taker_fees: Decimal
    """Taker fees the rebate is a share of: paid by you (taker) or generated by your fills (maker)."""
    rebate: Decimal
    """Exact rebate in USD (Polymarket documents no rounding for rebates)."""
    basis: str
    source_url: str
    rates_as_of: str
    notes: tuple[str, ...] = ()

    @property
    def rate(self) -> Decimal:
        return self.tier.rebate

    def breakdown(self) -> dict[str, str]:
        return {
            "role": self.role,
            "tier": self.tier.name,
            "rebate_rate": str(self.tier.rebate),
            "taker_fees_usd": str(self.taker_fees),
            "rebate_usd": str(self.rebate),
            "basis": self.basis,
            "source_url": self.source_url,
            "rates_as_of": self.rates_as_of,
        }


def polymarket_rebate(
    role: Role,
    taker_fees: Number,
    *,
    tier: str | None = None,
    weighted_volume_30d: Number | None = None,
    category: str | None = None,
    at: When | None = None,
    rates: Rates | None = None,
) -> Rebate:
    """polymarket.com rebate from 2026-11-20 23:59 UTC: the tier's share of ``taker_fees``.

    ``role="taker"``: ``taker_fees`` are the taker fees you paid; tiers None, Gold, Platinum,
    Diamond, Obsidian, Onyx. ``role="maker"``: ``taker_fees`` are the taker fees your eligible
    maker fills generated; tiers None, Tier 1-4. Give the tier by name (``tier="Tier 4"``) or
    by ``weighted_volume_30d`` (30-day weighted volume, see ``weighted_volume()``).
    ``category`` ``Combos`` or ``Geopolitics`` (fee-free) earn no maker rebate.

    Example from Polymarket: a Tier 4 maker whose fills generate $100 in taker fees earns $15.
    """
    r = rates if rates is not None else default_rates()
    period, tiers = _rebate_period(role, at, r)
    if (tier is None) == (weighted_volume_30d is None):
        raise ValueError("give either tier or weighted_volume_30d")
    if tier is not None:
        wanted = tier.lower().replace(" ", "")
        found = [t for t in tiers if t.name.lower().replace(" ", "") == wanted]
        if not found:
            raise ValueError(
                f"unknown {role} rebate tier {tier!r}; use one of: {', '.join(t.name for t in tiers)}"
            )
        chosen = found[0]
    else:
        chosen = polymarket_rebate_tier(role, weighted_volume_30d, at=at, rates=r)  # type: ignore[arg-type]
    fees = _num(taker_fees, "taker_fees")
    if fees < 0:
        raise ValueError("taker_fees must be >= 0")
    notes: list[str] = []
    amount = fees * chosen.rebate
    excluded = {c.lower() for c in period.no_maker_rebate_categories}
    if role == "maker" and category is not None and category.strip().lower() in excluded:
        amount = Decimal(0)
        notes.append(f"{category.strip()} fills earn no maker rebates.")
    if role == "maker":
        notes.append("Maker fills count only toward the maker tier.")
        basis = "share of the taker fees generated by your eligible maker fills"
    else:
        notes.append("Taker fills count only toward the taker tier.")
        basis = "share of the taker fees you paid"
    return Rebate(
        role=role,
        tier=chosen,
        taker_fees=fees,
        rebate=amount,
        basis=basis,
        source_url=period.source_url,
        rates_as_of=period.as_of,
        notes=tuple(notes),
    )
