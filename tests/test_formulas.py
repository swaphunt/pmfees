"""Lock the fee formulas to worked examples from each platform's own documentation.

These tests use a frozen copy of the rates file (``fixtures/rates_2026-11-08.json``), so they
check the formulas and rounding, not today's rates. An updated rates file can never break
them; ``test_rates.py`` checks the bundled file.
"""

from __future__ import annotations

import unittest
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import ClassVar

import pytest

from pmfees import break_even, fee, load_rates

RATES = load_rates(Path(__file__).parent / "fixtures" / "rates_2026-11-08.json")
D = Decimal


def cents(values: str) -> list[Decimal]:
    return [D(v) for v in values.split()]


class PolymarketUSTest(unittest.TestCase):
    """Source: Polymarket US Fee Schedule, https://docs.polymarket.us/fees (fetched 2026-11-10).

    The page's examples and price table use Θ = 0.0695, which is Tier 4 and the
    new-account coefficient in the tier ladder effective 2026-11-06 13:00 ET.
    """

    def test_standard_fee_examples_1_to_5(self) -> None:
        # "Standard Fee Examples", taker side of Examples 1-5.
        examples = [
            ("buy", "0.10", "6.26"),  # Example 1: 0.0695 × 1,000 × 0.10 × 0.90
            ("buy", "0.65", "15.81"),  # Example 2: 0.0695 × 1,000 × 0.65 × 0.35
            ("sell", "0.30", "14.60"),  # Example 3: 0.0695 × 1,000 × 0.30 × 0.70
            ("sell", "0.90", "6.26"),  # Example 4: 0.0695 × 1,000 × 0.90 × 0.10
            ("buy", "0.50", "17.38"),  # Example 5: 0.0695 × 1,000 × 0.50 × 0.50
        ]
        for side, price, expected in examples:
            for tier in (4, "new"):
                with self.subTest(side=side, price=price, tier=tier):
                    r = fee("polymarket-us", price, 1000, side, tier=tier, rates=RATES)  # type: ignore[arg-type]
                    self.assertEqual(r.fee, D(expected))

    def test_standard_fee_schedule_by_price_100_lot(self) -> None:
        # "Standard Fee Schedule by Price", column "Taker Pays (100-lot)", $0.01 to $0.99.
        table = cents(
            "0.07 0.14 0.20 0.27 0.33 0.39 0.45 0.51 0.57 0.63 0.68 0.73 0.79 0.84 0.89 0.93 0.98 1.03 "
            "1.07 1.11 1.15 1.19 1.23 1.27 1.30 1.34 1.37 1.40 1.43 1.46 1.49 1.51 1.54 1.56 1.58 1.60 "
            "1.62 1.64 1.65 1.67 1.68 1.69 1.70 1.71 1.72 1.73 1.73 1.73 1.74 1.74 1.74 1.73 1.73 1.73 "
            "1.72 1.71 1.70 1.69 1.68 1.67 1.65 1.64 1.62 1.60 1.58 1.56 1.54 1.51 1.49 1.46 1.43 1.40 "
            "1.37 1.34 1.30 1.27 1.23 1.19 1.15 1.11 1.07 1.03 0.98 0.93 0.89 0.84 0.79 0.73 0.68 0.63 "
            "0.57 0.51 0.45 0.39 0.33 0.27 0.20 0.14 0.07"
        )
        self.assertEqual(len(table), 99)
        for i, expected in enumerate(table, start=1):
            price = D(i) / 100
            with self.subTest(price=price):
                self.assertEqual(fee("polymarket-us", price, 100, tier=4, rates=RATES).fee, expected)

    def test_bankers_rounding(self) -> None:
        # FAQ "What is banker's rounding?": $0.025 -> $0.02, $0.035 -> $0.04.
        rounding = RATES.polymarket_us.rounding
        self.assertEqual(rounding.apply(D("0.025")), D("0.02"))
        self.assertEqual(rounding.apply(D("0.035")), D("0.04"))
        # Example 3 is an exact half: 14.595 -> 14.60; Example 1: 6.255 -> 6.26.
        self.assertEqual(fee("polymarket-us", "0.30", 1000, "sell", tier=4, rates=RATES).raw_fee, D("14.595"))

    def test_fee_can_round_to_zero(self) -> None:
        # FAQ "Can fees ever be zero?": small trades can round down to $0.00.
        self.assertEqual(fee("polymarket-us", "0.01", 1, tier=4, rates=RATES).fee, D("0.00"))

    def test_upcoming_taker_fee_tiers(self) -> None:
        # "Upcoming taker fee tiers": Θ 0.095 / 0.085 / 0.075 / 0.0695 by 30-day taker volume.
        for tier, theta in ((1, "0.095"), (2, "0.085"), (3, "0.075"), (4, "0.0695"), ("new", "0.0695")):
            with self.subTest(tier=tier):
                r = fee("polymarket-us", "0.50", 100, tier=tier, rates=RATES)
                self.assertEqual(r.coefficient, D(theta))
        volumes: list[tuple[int | str, str]] = [
            (0, "0.095"),
            ("9999.99", "0.095"),
            (10000, "0.085"),  # "$10,000 to less than $50,000"
            (49999, "0.085"),
            (50000, "0.075"),
            (250000, "0.0695"),  # "$250,000 or more"
            (10_000_000, "0.0695"),
        ]
        for vol, theta in volumes:
            with self.subTest(volume=vol):
                r = fee("polymarket-us", "0.50", 100, volume_30d=vol, rates=RATES)
                self.assertEqual(r.coefficient, D(theta))

    def test_tier_1_at_50c_is_475_percent(self) -> None:
        # Θ × (1 − p) of trade value: 0.095 × 0.5 = 4.75% (100 × $0.50 -> $2.375 -> $2.38).
        r = fee("polymarket-us", "0.50", 100, tier=1, rates=RATES)
        self.assertEqual(r.raw_fee, D("2.375"))
        self.assertEqual(r.fee, D("2.38"))

    def test_makers_pay_no_fee(self) -> None:
        self.assertEqual(fee("polymarket-us", "0.50", 1000, maker=True, rates=RATES).fee, 0)


class PolymarketGlobalTest(unittest.TestCase):
    """Source: polymarket.com Fees, https://docs.polymarket.com/trading/fees (fetched 2026-11-10).

    The "Fee Tables (100 Shares)" show fees to the cent; the fee itself is rounded to
    5 decimals ("rounded to 5 decimals (minimum 0.00001 USDC)").
    """

    PRICES: ClassVar[list[Decimal]] = [D("0.01")] + [D(n) / 100 for n in range(5, 96, 5)] + [D("0.99")]
    TABLES: ClassVar[dict[str, str]] = {
        "Crypto": "0.07 0.33 0.63 0.89 1.12 1.31 1.47 1.59 1.68 1.73 1.75 1.73 1.68 1.59 1.47 1.31 "
        "1.12 0.89 0.63 0.33 0.07",
        "Sports": "0.05 0.24 0.45 0.64 0.80 0.94 1.05 1.14 1.20 1.24 1.25 1.24 1.20 1.14 1.05 0.94 "
        "0.80 0.64 0.45 0.24 0.05",
        "Finance Politics Mentions Tech": "0.04 0.19 0.36 0.51 0.64 0.75 0.84 0.91 0.96 0.99 1.00 0.99 "
        "0.96 0.91 0.84 0.75 0.64 0.51 0.36 0.19 0.04",
        "Economics Culture Weather Other": "0.05 0.24 0.45 0.64 0.80 0.94 1.05 1.14 1.20 1.24 1.25 1.24 "
        "1.20 1.14 1.05 0.94 0.80 0.64 0.45 0.24 0.05",
    }

    def test_fee_tables_100_shares(self) -> None:
        for cats, values in self.TABLES.items():
            table = cents(values)
            self.assertEqual(len(table), len(self.PRICES))
            for category in cats.split():
                for price, expected in zip(self.PRICES, table, strict=True):
                    with self.subTest(category=category, price=price):
                        r = fee("polymarket", price, 100, category=category, rates=RATES)
                        self.assertEqual(r.fee.quantize(D("0.01"), rounding=ROUND_HALF_UP), expected)

    def test_five_decimal_rounding(self) -> None:
        # 100 Crypto shares at $0.25: 0.07 × 100 × 0.25 × 0.75 = 1.3125 exactly (table shows $1.31).
        self.assertEqual(fee("polymarket", "0.25", 100, category="Crypto", rates=RATES).fee, D("1.31250"))
        # 1 Crypto share at $0.01: 0.000693 -> 0.00069.
        self.assertEqual(fee("polymarket", "0.01", 1, category="Crypto", rates=RATES).fee, D("0.00069"))

    def test_geopolitics_is_fee_free_and_makers_never_pay(self) -> None:
        # "Geopolitical and world events markets are fee-free." / "Makers are never charged fees."
        self.assertEqual(fee("polymarket", "0.50", 100, category="Geopolitics", rates=RATES).fee, 0)
        self.assertEqual(fee("polymarket", "0.50", 100, maker=True, category="Crypto", rates=RATES).fee, 0)

    def test_symmetric_around_50c(self) -> None:
        # "a trade at 30¢ incurs the same dollar fee as a trade at 70¢"
        a = fee("polymarket", "0.30", 100, category="Sports", rates=RATES).fee
        b = fee("polymarket", "0.70", 100, category="Sports", rates=RATES).fee
        self.assertEqual(a, b)


KALSHI_SOURCE = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
KALSHI_VERSION = "7.7.26"


class KalshiTest(unittest.TestCase):
    """Source: Kalshi Fee Schedule, https://kalshi.com/docs/kalshi-fee-schedule.pdf,
    "Fee Schedule for July 2026 - 7.7.26 Update", page 2:

    * Taker: fees = round up(M × 0.07 × C × P × (1 − P)); M defaults to 1.
    * Maker: fees = round up(M × 0.0175 × C × P × (1 − P)); M defaults to 0.
    * "round up = rounds up such that the fee + positionCost is rounded to a centicent".
    """

    def test_source_is_the_rates_file_source(self) -> None:
        self.assertEqual(RATES.kalshi.source_url, KALSHI_SOURCE)

    def test_taker_100_contracts(self) -> None:
        # 0.07 × 100 × P × (1 − P), already whole centicents, so no rounding.
        for price, expected in (
            ("0.10", "0.63"),
            ("0.25", "1.3125"),
            ("0.50", "1.75"),
            ("0.75", "1.3125"),
            ("0.90", "0.63"),
        ):
            with self.subTest(price=price, version=KALSHI_VERSION):
                self.assertEqual(fee("kalshi", price, 100, rates=RATES).fee, D(expected))

    def test_rounded_up_to_a_centicent_not_a_cent(self) -> None:
        # One contract at 50¢: 0.07 × 0.5 × 0.5 = 0.0175 stays $0.0175 (not $0.02).
        self.assertEqual(fee("kalshi", "0.50", 1, rates=RATES).fee, D("0.0175"))
        # 100 at 54¢: 0.07 × 100 × 0.54 × 0.46 = 1.7388 exactly.
        self.assertEqual(fee("kalshi", "0.54", 100, rates=RATES).fee, D("1.7388"))
        # 100 at 53¢: 1.7437 exactly; 1 at 1¢: 0.000693 -> 0.0007 (up).
        self.assertEqual(fee("kalshi", "0.53", 100, rates=RATES).fee, D("1.7437"))
        self.assertEqual(fee("kalshi", "0.01", 1, rates=RATES).fee, D("0.0007"))
        # 3 at 33¢: 0.07 × 3 × 0.33 × 0.67 = 0.046431 -> 0.0465.
        self.assertEqual(fee("kalshi", "0.33", 3, rates=RATES).fee, D("0.0465"))

    def test_fee_plus_position_cost_is_a_whole_centicent(self) -> None:
        # Sub-centicent position cost: 1 contract at $0.12345 costs 0.12345.
        # Fee 0.07 × 0.12345 × 0.87655 = 0.00757478...; 0.12345 + 0.00757478 = 0.13102478
        # rounds up to 0.1311, so the fee is 0.00765 (rounding the fee alone would give 0.0076).
        r = fee("kalshi", "0.12345", 1, rates=RATES)
        self.assertEqual(r.fee, D("0.00765"))
        self.assertEqual((r.trade_value + r.fee) % D("0.0001"), 0)

    def test_maker_fee(self) -> None:
        # M defaults to 0 for makers: no fee unless the market lists a maker multiplier.
        self.assertEqual(fee("kalshi", "0.50", 100, maker=True, rates=RATES).fee, 0)
        # With M = 1: 0.0175 × 100 × 0.50 × 0.50 = 0.4375; at 25¢ 0.328125 rounds up to 0.3282.
        self.assertEqual(fee("kalshi", "0.50", 100, maker=True, multiplier=1, rates=RATES).fee, D("0.4375"))
        self.assertEqual(fee("kalshi", "0.25", 100, maker=True, multiplier=1, rates=RATES).fee, D("0.3282"))

    def test_multiplier(self) -> None:
        # Taker M defaults to 1; M = 0.5 halves the fee: 0.875; M = 0: fee-free.
        self.assertEqual(fee("kalshi", "0.50", 100, rates=RATES).coefficient, D("0.07"))
        self.assertEqual(fee("kalshi", "0.50", 100, multiplier="0.5", rates=RATES).fee, D("0.875"))
        self.assertEqual(fee("kalshi", "0.50", 100, multiplier=0, rates=RATES).fee, 0)
        with self.assertRaises(ValueError):
            fee("kalshi", "0.50", 100, multiplier="0.25", rates=RATES)


class BreakEvenTest(unittest.TestCase):
    def test_kalshi_buy_50c(self) -> None:
        # Kalshi Fee Schedule 7.7.26 (centicent rounding). Buy 100 at 50¢: $50 + $1.75.
        # At 53¢: 53 − 1.7437 − 51.75 = −0.4937. At 54¢: 54 − 1.7388 − 51.75 = +0.5112.
        b = break_even("kalshi", "0.50", 100, rates=RATES)
        self.assertEqual(
            (b.exit_price, b.move, b.exit_fee, b.profit_at_exit),
            (D("0.54"), D("0.04"), D("1.7388"), D("0.5112")),
        )

    def test_polymarket_us_sell(self) -> None:
        # Sell 1,000 at 30¢ at Tier 4 (Example 3: fee $14.60): receive $300 − 14.60.
        # Buying back at 28¢ costs 280 + 14.01 (14.0112) -> −8.61; at 27¢ 270 + 13.70 (13.69845) -> +1.70.
        b = break_even("polymarket-us", "0.30", 1000, "sell", tier=4, rates=RATES)
        self.assertEqual(b.exit_price, D("0.27"))
        self.assertEqual(b.move, D("-0.03"))
        self.assertEqual(b.profit_at_exit, D("1.70"))

    def test_no_fee_breaks_even_after_one_tick(self) -> None:
        b = break_even("polymarket", "0.40", 10, category="Geopolitics", rates=RATES)
        self.assertEqual(b.move, D("0.01"))

    def test_none_when_no_exit_price_works(self) -> None:
        b = break_even("kalshi", "0.99", 1, rates=RATES)
        self.assertIsNone(b.exit_price)
        self.assertIsNone(b.move)


class InputTest(unittest.TestCase):
    def test_float_inputs_are_exact(self) -> None:
        # 0.1 as a float must be 0.1, not 0.1000000000000000055...
        self.assertEqual(fee("kalshi", 0.1, 100, rates=RATES).raw_fee, D("0.6300"))

    def test_rejects_bad_input(self) -> None:
        bad: list[dict[str, object]] = [
            {"platform": "kalshi", "price": 0},
            {"platform": "kalshi", "price": 1},
            {"platform": "kalshi", "price": "0.5", "contracts": 0},
            {"platform": "nasdaq", "price": "0.5"},
            {"platform": "polymarket", "price": "0.5"},  # category missing
            {"platform": "polymarket", "price": "0.5", "category": "Astrology"},
            {"platform": "polymarket-us", "price": "0.5", "tier": 5},
            {"platform": "kalshi", "price": "0.5", "tier": 1},
        ]
        for case in bad:
            with self.subTest(case=case), self.assertRaises(ValueError):
                fee(
                    str(case["platform"]),
                    str(case["price"]),
                    str(case.get("contracts", 10)),
                    tier=case.get("tier"),  # type: ignore[arg-type]
                    category=case.get("category"),  # type: ignore[arg-type]
                    rates=RATES,
                )

    def test_platform_aliases(self) -> None:
        for name in ("polymarket.com", "Polymarket", "polymarket_global"):
            self.assertEqual(
                fee(name, "0.5", 100, category="Crypto", rates=RATES).platform_name, "polymarket.com"
            )
        self.assertEqual(fee("polymarket_us", "0.5", 100, rates=RATES).platform_name, "Polymarket US")


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize(
    ("platform", "contracts", "kwargs"),
    [
        ("kalshi", "1.5", {}),
        ("kalshi", "0.99", {}),
        ("polymarket-us", "153.845", {}),
        ("polymarket", "19.2307001", {"category": "Crypto"}),
    ],
)
def test_contracts_off_the_platform_step_raise(platform, contracts, kwargs):
    with pytest.raises(ValueError, match="contracts must be a multiple of"):
        fee(platform, "0.50", contracts, **kwargs)


@pytest.mark.parametrize(
    ("platform", "contracts", "kwargs"),
    [
        ("kalshi", "7", {}),
        ("kalshi", "7.0", {}),
        ("polymarket-us", "153.84", {}),
        ("polymarket", "19.2307", {"category": "Crypto"}),
    ],
)
def test_contracts_on_the_platform_step_are_fine(platform, contracts, kwargs):
    assert fee(platform, "0.50", contracts, **kwargs).fee >= 0
