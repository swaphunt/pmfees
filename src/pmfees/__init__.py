"""pmfees: exact trading fees for Kalshi, Polymarket US and polymarket.com.

All rates, tiers, multipliers and rounding rules come from ``prediction_market_fees.json``
(bundled, or your own file via ``load_rates(path)`` / ``PMFEES_RATES_FILE``).
"""

from __future__ import annotations

__version__ = "0.1.1"

from .core import PLATFORMS, BreakEven, FeeResult, break_even, fee, platform_key
from .rates import Rates, RatesError, default_rates, load_rates

__all__ = [
    "PLATFORMS",
    "BreakEven",
    "FeeResult",
    "Rates",
    "RatesError",
    "__version__",
    "break_even",
    "default_rates",
    "fee",
    "load_rates",
    "platform_key",
]
