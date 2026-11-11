"""Load fee rates from ``prediction_market_fees.json``.

The JSON file is the only place rates, tiers, multipliers and rounding rules live.
Nothing in this package hardcodes a rate: pass ``path=`` (or set ``PMFEES_RATES_FILE``)
to use an updated file without a new release.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
class PolymarketGlobal:
    name: str
    source_url: str
    formula: str
    rates: dict[str, Decimal]
    rounding: Rounding
    maker_rebate_share: dict[str, Decimal]
    notes: tuple[str, ...]
    as_of: str = ""
    """Date these rates were last checked against ``source_url``."""


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
