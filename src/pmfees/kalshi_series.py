"""Per-series Kalshi fee structure: ``fee_type`` and multiplier M for each series.

Kalshi lists a ``fee_type`` and ``fee_multiplier`` for every series in its Trade API
(Get Series List). Most series are standard (``quadratic``, M = 1); ``kalshi_series.json``
lists the others, with the date of the API response and links to the API and the fee schedule.
A series that is not listed is standard.

Rebuild the file with ``scripts/update_kalshi_series.py``. Pass ``path=`` (or set
``PMFEES_KALSHI_SERIES_FILE``) to use an updated file without a new release.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from importlib import resources
from pathlib import Path
from typing import Any

from .rates import RatesError

ENV_VAR = "PMFEES_KALSHI_SERIES_FILE"


@dataclass(frozen=True)
class KalshiSeries:
    """Fee structure of one Kalshi series."""

    ticker: str
    fee_type: str
    multiplier: Decimal
    """The series' fee multiplier M (1, 0.5 or 0)."""
    maker_share: Decimal
    """Maker fee as a share of the taker fee before M: 0 (no maker fees), 0.25 or 0.5 (combos)."""
    title: str = ""
    listed: bool = True
    """False when the ticker is not in the file and the default (standard) structure was assumed."""


@dataclass(frozen=True)
class KalshiSeriesTable:
    as_of: str
    """Date of the Get Series List response the file was built from."""
    source_url: str
    source_doc_url: str
    fee_schedule_url: str
    fee_schedule_version: str
    fee_schedule_date: str
    series_total: int
    """Number of series in that response, standard ones included."""
    default_fee_type: str
    default_multiplier: Decimal
    maker_shares: dict[str, Decimal]
    series: dict[str, KalshiSeries]
    path: str

    def lookup(self, ticker: str) -> KalshiSeries:
        """Fee structure for a series ticker (``KXMLBGAME``), or an event or market ticker in
        it (``KXMLBGAME-26NOV14NYYBOS-NYY``): the longest listed prefix ending at a ``-`` wins.
        Tickers that match nothing get the default structure with ``listed=False``."""
        t = ticker.strip().upper()
        if not t:
            raise ValueError("series ticker must not be empty")
        probe = t
        while True:
            hit = self.series.get(probe)
            if hit is not None:
                return hit
            if "-" not in probe:
                break
            probe = probe.rsplit("-", 1)[0]
        return KalshiSeries(
            ticker=t,
            fee_type=self.default_fee_type,
            multiplier=self.default_multiplier,
            maker_share=self.maker_shares[self.default_fee_type],
            listed=False,
        )


def _dec(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise RatesError(f"{where}: expected a number, got {value!r}")
    return Decimal(str(value))


def parse_kalshi_series(data: dict[str, Any], label: str = "kalshi_series.json") -> KalshiSeriesTable:
    try:
        fee_types = data["fee_types"]
        shares = {
            name: _dec(ft["maker_share"], f"{label}: fee_types.{name}") for name, ft in fee_types.items()
        }
        default = data["default"]
        default_type = str(default["fee_type"])
        if default_type not in shares:
            raise RatesError(f"{label}: default fee_type {default_type!r} is not in fee_types")
        series: dict[str, KalshiSeries] = {}
        for ticker, s in data["series"].items():
            ft = str(s["fee_type"])
            if ft not in shares:
                raise RatesError(f"{label}: {ticker}: fee_type {ft!r} is not in fee_types")
            series[ticker.upper()] = KalshiSeries(
                ticker=ticker.upper(),
                fee_type=ft,
                multiplier=_dec(s["multiplier"], f"{label}: {ticker}.multiplier"),
                maker_share=shares[ft],
                title=str(s.get("title", "")),
            )
        return KalshiSeriesTable(
            as_of=str(data["as_of"]),
            source_url=str(data["source_url"]),
            source_doc_url=str(data.get("source_doc_url", "")),
            fee_schedule_url=str(data["fee_schedule_url"]),
            fee_schedule_version=str(data.get("fee_schedule_version", "")),
            fee_schedule_date=str(data.get("fee_schedule_date", "")),
            series_total=int(data.get("series_total", 0)),
            default_fee_type=default_type,
            default_multiplier=_dec(default["multiplier"], f"{label}: default.multiplier"),
            maker_shares=shares,
            series=series,
            path=label,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise RatesError(f"{label}: missing or malformed field ({exc!r})") from exc


def load_kalshi_series(path: str | os.PathLike[str] | None = None) -> KalshiSeriesTable:
    """Load the series file from ``path``, else ``$PMFEES_KALSHI_SERIES_FILE``, else the bundled file."""
    chosen = path if path is not None else os.environ.get(ENV_VAR) or None
    if chosen is not None:
        p = Path(chosen)
        text, label = p.read_text(encoding="utf-8"), str(p)
    else:
        text = resources.files("pmfees").joinpath("data/kalshi_series.json").read_text(encoding="utf-8")
        label = "bundled kalshi_series.json"
    try:
        data = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise RatesError(f"{label}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise RatesError(f"{label}: expected a JSON object")
    return parse_kalshi_series(data, label)


_default: KalshiSeriesTable | None = None


def default_kalshi_series() -> KalshiSeriesTable:
    """The series table used when no ``series_table=`` argument is given (loaded once)."""
    global _default
    if _default is None:
        _default = load_kalshi_series()
    return _default


def kalshi_series(ticker: str, series_table: KalshiSeriesTable | None = None) -> KalshiSeries:
    """Fee structure for a Kalshi series, event or market ticker (see ``KalshiSeriesTable.lookup``)."""
    return (series_table if series_table is not None else default_kalshi_series()).lookup(ticker)
