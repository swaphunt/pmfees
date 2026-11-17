"""Load fee rates from ``prediction_market_fees.json``.

The JSON file is the only place rates, tiers, multipliers and rounding rules live.
Nothing in this package hardcodes a rate: pass ``path=`` (or set ``PMFEES_RATES_FILE``)
to use an updated file without a new release.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal
from importlib import resources
from pathlib import Path
from typing import Any

ENV_VAR = "PMFEES_RATES_FILE"

_ROUNDING_MODES = {"up": ROUND_UP, "half_even": ROUND_HALF_EVEN, "half_up": ROUND_HALF_UP}
_ROUNDING_WORDS = {
    "up": "rounded up",
    "half_even": "rounded half to even (banker's rounding)",
    "half_up": "rounded half up",
}


class RatesError(ValueError):
    """The rates file is missing a field or has a value the package cannot use."""


BASES = (None, "fee_plus_position_cost")


@dataclass(frozen=True)
class Rounding:
    decimals: int
    mode: str
    note: str
    basis: str | None = None
    """``fee_plus_position_cost`` (Kalshi): fee + position cost (C × P) is rounded, not the fee alone."""

    def apply(self, value: Decimal, position_cost: Decimal = Decimal(0)) -> Decimal:
        q = Decimal(1).scaleb(-self.decimals)
        if self.basis == "fee_plus_position_cost":
            return (position_cost + value).quantize(q, rounding=_ROUNDING_MODES[self.mode]) - position_cost
        return value.quantize(q, rounding=_ROUNDING_MODES[self.mode])

    def describe(self) -> str:
        unit = {2: "the cent", 4: "a whole centicent ($0.0001)"}.get(
            self.decimals, f"{self.decimals} decimals"
        )
        if self.basis == "fee_plus_position_cost":
            return f"{_ROUNDING_WORDS[self.mode]} so that fee + position cost is {unit}"
        return f"{_ROUNDING_WORDS[self.mode]} to {unit}"


@dataclass(frozen=True)
class UsTier:
    number: int
    below: Decimal | None
    theta: Decimal

    @property
    def label(self) -> str:
        return f"Tier {self.number}"


@dataclass(frozen=True)
class RebateTier:
    """A polymarket.com rebate tier by 30-day weighted volume (shares × price × (1 − price))."""

    name: str
    minimum: Decimal
    below: Decimal | None
    rebate: Decimal
    """Share of the taker fees the tier pays back, e.g. 0.15 for 15%."""


@dataclass(frozen=True)
class GlobalPeriod:
    """polymarket.com rates in force from ``effective`` (None: before the first dated change)."""

    effective: datetime | None
    source_url: str
    as_of: str
    rates: dict[str, Decimal]
    notes: tuple[str, ...]
    rate_labels: dict[str, str] = field(default_factory=dict)
    taker_rebate_tiers: tuple[RebateTier, ...] = ()
    maker_rebate_tiers: tuple[RebateTier, ...] = ()
    no_maker_rebate_categories: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class PolymarketGlobal:
    name: str
    source_url: str
    formula: str
    rates: dict[str, Decimal]
    """Rates before the first entry in ``schedule`` (or always, when there is none)."""
    rounding: Rounding
    maker_rebate_share: dict[str, Decimal]
    notes: tuple[str, ...]
    as_of: str = ""
    """Date these rates were last checked against ``source_url``."""
    schedule: tuple[GlobalPeriod, ...] = ()
    """Dated changes, oldest first: a fill at or after ``effective`` uses that period."""

    @property
    def base_period(self) -> GlobalPeriod:
        return GlobalPeriod(
            effective=None, source_url=self.source_url, as_of=self.as_of, rates=self.rates, notes=self.notes
        )

    def period_at(self, when: datetime) -> GlobalPeriod:
        """The rates in force at ``when`` (timezone-aware)."""
        current = self.base_period
        for period in self.schedule:
            if period.effective is not None and when >= period.effective:
                current = period
        return current


@dataclass(frozen=True)
class PolymarketUS:
    name: str
    source_url: str
    effective: str
    formula: str
    tiers: tuple[UsTier, ...]
    new_account_theta: Decimal
    new_account_note: str
    makers: str
    rounding: Rounding
    notes: tuple[str, ...]
    as_of: str = ""
    """Date these rates were last checked against ``source_url``."""


@dataclass(frozen=True)
class Kalshi:
    name: str
    source_url: str
    source_date: str
    formula: str
    taker_rate: Decimal
    maker_rate: Decimal
    multipliers: tuple[Decimal, ...]
    rounding: Rounding
    rounding_source_url: str | None
    notes: tuple[str, ...]
    as_of: str = ""
    """Date these rates were last checked against ``source_url``."""
    version: str = ""
    """Version of the Kalshi fee schedule the rates come from (for example ``7.7.26``)."""


@dataclass(frozen=True)
class Rates:
    as_of: str
    path: str
    polymarket_global: PolymarketGlobal
    polymarket_us: PolymarketUS
    kalshi: Kalshi


def _get(obj: dict[str, Any], key: str, where: str) -> Any:
    if key not in obj:
        raise RatesError(f"{where}: missing field {key!r}")
    return obj[key]


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise RatesError(f"{where}: expected a number, got {value!r}")
    d = Decimal(value)
    if d < 0:
        raise RatesError(f"{where}: negative value {value!r}")
    return d


def _effective(value: Any, where: str) -> datetime:
    text = str(value).strip()
    try:
        when = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise RatesError(f"{where}: not an ISO 8601 time: {value!r}") from exc
    if when.tzinfo is None:
        raise RatesError(f"{where}: the time needs a timezone, e.g. 2026-11-20T23:59:00Z")
    return when.astimezone(timezone.utc)


def _rebate_tiers(raw: Any, where: str) -> tuple[RebateTier, ...]:
    if raw is None:
        return ()
    tiers: list[RebateTier] = []
    minimum = Decimal(0)
    for i, t in enumerate(raw):
        w = f"{where}[{i}]"
        below = _get(t, "below", w)
        b = None if below is None else _dec(below, w)
        if b is not None and b <= minimum:
            raise RatesError(f"{w}: tiers must be ordered by volume")
        tiers.append(RebateTier(str(_get(t, "name", w)), minimum, b, _dec(_get(t, "rebate", w), w)))
        if b is None and i != len(raw) - 1:
            raise RatesError(f"{w}: only the last tier can have below = null")
        minimum = b if b is not None else minimum
    if tiers and tiers[-1].below is not None:
        raise RatesError(f"{where}: the last tier must have below = null")
    return tuple(tiers)


def _schedule(raw: Any) -> tuple[GlobalPeriod, ...]:
    periods: list[GlobalPeriod] = []
    for i, e in enumerate(raw or ()):
        w = f"polymarket_global.schedule[{i}]"
        when = _effective(_get(e, "effective", w), f"{w}.effective")
        if periods and periods[-1].effective is not None and when <= periods[-1].effective:
            raise RatesError(f"{w}: schedule must be ordered by effective time")
        periods.append(
            GlobalPeriod(
                effective=when,
                source_url=str(_get(e, "source_url", w)),
                as_of=str(e.get("as_of", "")),
                rates={c: _dec(r, f"{w}.rates.{c}") for c, r in _get(e, "rates", w).items()},
                notes=tuple(e.get("notes", ())),
                rate_labels={str(k): str(v) for k, v in e.get("rate_labels", {}).items()},
                taker_rebate_tiers=_rebate_tiers(
                    e.get("taker_rebate_tiers_30d_weighted_volume_usd"), f"{w}.taker_rebate_tiers"
                ),
                maker_rebate_tiers=_rebate_tiers(
                    e.get("maker_rebate_tiers_30d_weighted_volume_usd"), f"{w}.maker_rebate_tiers"
                ),
                no_maker_rebate_categories=tuple(str(c) for c in e.get("no_maker_rebate_categories", ())),
                summary=str(e.get("summary", "")),
            )
        )
    return tuple(periods)


def _rounding(obj: dict[str, Any], where: str) -> Rounding:
    mode = _get(obj, "mode", where)
    if mode not in _ROUNDING_MODES:
        raise RatesError(f"{where}: unknown rounding mode {mode!r}")
    decimals = _get(obj, "decimals", where)
    if not isinstance(decimals, int) or decimals < 0:
        raise RatesError(f"{where}: decimals must be a non-negative integer")
    basis = obj.get("basis")
    if basis not in BASES:
        raise RatesError(f"{where}: unknown rounding basis {basis!r}")
    return Rounding(decimals=decimals, mode=mode, note=str(obj.get("note", "")), basis=basis)


def _new_account_theta(text: str, where: str) -> Decimal:
    # The file states it as text, e.g. "Θ = 0.0695 for the first 7 days".
    for token in text.replace("=", " ").split():
        try:
            return _dec(Decimal(token), where)
        except ArithmeticError:
            continue
    raise RatesError(f"{where}: no coefficient found in {text!r}")


def parse_rates(data: dict[str, Any], path: str = "<memory>") -> Rates:
    """Build :class:`Rates` from the decoded JSON (numbers decoded as ``Decimal``)."""
    g = _get(data, "polymarket_global", "root")
    us = _get(data, "polymarket_us", "root")
    k = _get(data, "kalshi", "root")

    tiers: list[UsTier] = []
    raw_tiers = _get(us, "taker_tiers_30d_volume_usd", "polymarket_us")
    for i, t in enumerate(raw_tiers, start=1):
        where = f"polymarket_us.taker_tiers_30d_volume_usd[{i - 1}]"
        below = _get(t, "below", where)
        tiers.append(
            UsTier(
                number=i,
                below=None if below is None else _dec(below, where),
                theta=_dec(_get(t, "theta", where), where),
            )
        )
    if not tiers or tiers[-1].below is not None:
        raise RatesError("polymarket_us: the last tier must have below = null")

    new_accounts = str(_get(us, "new_accounts", "polymarket_us"))
    rounding_k = _get(k, "rounding", "kalshi")
    as_of = str(_get(data, "as_of", "root"))

    return Rates(
        as_of=as_of,
        path=path,
        polymarket_global=PolymarketGlobal(
            name=str(_get(g, "name", "polymarket_global")),
            source_url=str(_get(g, "source_url", "polymarket_global")),
            formula=str(_get(g, "formula", "polymarket_global")),
            rates={
                cat: _dec(r, f"polymarket_global.rates.{cat}")
                for cat, r in _get(g, "rates", "polymarket_global").items()
            },
            rounding=_rounding(_get(g, "rounding", "polymarket_global"), "polymarket_global.rounding"),
            maker_rebate_share={
                cat: _dec(r, f"polymarket_global.maker_rebate_share.{cat}")
                for cat, r in g.get("maker_rebate_share", {}).items()
            },
            notes=tuple(g.get("notes", ())),
            as_of=str(g.get("as_of") or as_of),
            schedule=_schedule(g.get("schedule")),
        ),
        polymarket_us=PolymarketUS(
            name=str(_get(us, "name", "polymarket_us")),
            source_url=str(_get(us, "source_url", "polymarket_us")),
            effective=str(us.get("effective", "")),
            formula=str(_get(us, "formula", "polymarket_us")),
            tiers=tuple(tiers),
            new_account_theta=_new_account_theta(new_accounts, "polymarket_us.new_accounts"),
            new_account_note=new_accounts,
            makers=str(us.get("makers", "")),
            rounding=_rounding(_get(us, "rounding", "polymarket_us"), "polymarket_us.rounding"),
            notes=tuple(us.get("notes", ())),
            as_of=str(us.get("as_of") or as_of),
        ),
        kalshi=Kalshi(
            name=str(_get(k, "name", "kalshi")),
            source_url=str(_get(k, "source_url", "kalshi")),
            source_date=str(k.get("source_date", "")),
            formula=str(_get(k, "formula", "kalshi")),
            taker_rate=_dec(_get(k, "taker_rate", "kalshi"), "kalshi.taker_rate"),
            maker_rate=_dec(_get(k, "maker_rate", "kalshi"), "kalshi.maker_rate"),
            multipliers=tuple(_dec(m, "kalshi.multipliers") for m in _get(k, "multipliers", "kalshi")),
            rounding=_rounding(rounding_k, "kalshi.rounding"),
            rounding_source_url=rounding_k.get("source_url"),
            notes=tuple(k.get("notes", ())),
            as_of=str(k.get("as_of") or as_of),
            version=str(k.get("version", "")),
        ),
    )


def load_rates(path: str | os.PathLike[str] | None = None) -> Rates:
    """Load rates from ``path``, else ``$PMFEES_RATES_FILE``, else the bundled file."""
    chosen = path if path is not None else os.environ.get(ENV_VAR) or None
    if chosen is not None:
        p = Path(chosen)
        text = p.read_text(encoding="utf-8")
        label = str(p)
    else:
        res = resources.files("pmfees").joinpath("data/prediction_market_fees.json")
        text = res.read_text(encoding="utf-8")
        label = "bundled prediction_market_fees.json"
    try:
        data = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise RatesError(f"{label}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise RatesError(f"{label}: expected a JSON object")
    return parse_rates(data, label)


_default: Rates | None = None


def default_rates() -> Rates:
    """The rates used when no ``rates=`` argument is given (loaded once)."""
    global _default
    if _default is None:
        _default = load_rates()
    return _default
