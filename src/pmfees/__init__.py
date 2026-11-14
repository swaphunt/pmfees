"""pmfees: exact trading fees for Kalshi, Polymarket US and polymarket.com.

All rates, tiers, multipliers and rounding rules come from ``prediction_market_fees.json``
(bundled, or your own file via ``load_rates(path)`` / ``PMFEES_RATES_FILE``).
"""

from __future__ import annotations

__version__ = "0.2.0"

from .core import PLATFORMS, BreakEven, FeeResult, FillFees, break_even, fee, fill_fees, platform_key
from .kalshi_series import (
    KalshiSeries,
    KalshiSeriesTable,
    default_kalshi_series,
    kalshi_series,
    load_kalshi_series,
)
from .rates import Rates, RatesError, default_rates, load_rates

__all__ = [
    "PLATFORMS",
    "BreakEven",
    "FeeResult",
    "FillFees",
    "KalshiSeries",
    "KalshiSeriesTable",
    "Rates",
    "RatesError",
    "__version__",
    "break_even",
    "default_kalshi_series",
    "default_rates",
    "fee",
    "fill_fees",
    "kalshi_series",
    "load_kalshi_series",
    "load_rates",
    "platform_key",
]
