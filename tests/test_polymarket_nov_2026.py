"""polymarket.com fee and rebate changes effective 2026-11-20 23:59 UTC.

Source: https://docs.polymarket.com/programs/fee-and-rebate-changes-november-2026
"""

from __future__ import annotations

import contextlib
import io
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from pmfees import (
    fee,
    fill_fees,
    load_rates,
    polymarket_rebate,
    polymarket_rebate_tier,
    weighted_volume,
)
from pmfees.cli import main

EFFECTIVE = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)
BEFORE = EFFECTIVE - timedelta(seconds=1)
AFTER = EFFECTIVE
RATES = load_rates()

# The coefficient table from the source, row by row.
NEW_RATES = {
    "Crypto (BTC 5m/15m up/down)": "0.05",
    "Crypto": "0.01",
    "Sports": "0.05",
    "Finance": "0.05",
    "Tech": "0.05",
    "Politics (elections)": "0.05",
    "Politics": "0.01",
    "Economics": "0.01",
    "Culture": "0.01",
    "Weather": "0.01",
    "Mentions": "0.01",
    "Geopolitics": "0",
    "Combos": "0.05",
    "Other": "0.05",
}


class ScheduleTest(unittest.TestCase):
    def test_new_coefficients_match_the_source(self) -> None:
        period = RATES.polymarket_global.period_at(AFTER)
        self.assertEqual(period.effective, EFFECTIVE)
        self.assertEqual({k: v for k, v in period.rates.items()}, {k: D(v) for k, v in NEW_RATES.items()})
        for category, rate in NEW_RATES.items():
            with self.subTest(category=category):
                r = fee("polymarket", "0.40", 100, category=category, at=AFTER, rates=RATES)
                self.assertEqual(r.coefficient, D(rate))
                self.assertEqual(r.raw_fee, D(rate) * 100 * D("0.40") * D("0.60"))

    def test_fill_time_picks_the_rates(self) -> None:
        old = fee("polymarket", "0.25", 100, category="Crypto", at=BEFORE, rates=RATES)
        new = fee("polymarket", "0.25", 100, category="Crypto", at=AFTER, rates=RATES)
        self.assertEqual((old.coefficient, old.fee), (D("0.07"), D("1.31250")))
        self.assertEqual((new.coefficient, new.fee), (D("0.01"), D("0.18750")))
        self.assertEqual(old.details["rate_period"], "before 2026-11-20 23:59 UTC")
        self.assertEqual(new.details["rate_period"], "from 2026-11-20 23:59 UTC")
        self.assertEqual(old.source_url, "https://docs.polymarket.com/trading/fees")
        self.assertIn("fee-and-rebate-changes-november-2026", new.source_url)

    def test_time_formats(self) -> None:
        def coef(at: object) -> D:
            return fee("polymarket", "0.5", 10, category="Crypto", at=at, rates=RATES).coefficient  # type: ignore[arg-type]

        self.assertEqual(coef("2026-11-20T23:59:00Z"), D("0.01"))
        self.assertEqual(coef("2026-11-20T23:58:59Z"), D("0.07"))
        # 00:30 in UTC+01:00 is 23:30 UTC: still the old rates.
        self.assertEqual(coef("2026-11-21T00:30:00+01:00"), D("0.07"))
        with self.assertRaisesRegex(ValueError, "timezone"):
            coef(datetime(2026, 11, 21, 8, 0))
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            coef("tomorrow")

    def test_subcategory_falls_back_to_parent_before_the_change(self) -> None:
        r = fee("polymarket", "0.5", 100, category="Politics (elections)", at=BEFORE, rates=RATES)
        self.assertEqual((r.details["category"], r.coefficient), ("Politics", D("0.04")))
        self.assertTrue(any("uses the Politics rate" in n for n in r.notes))
        with self.assertRaisesRegex(ValueError, "unknown polymarket.com category"):
            fee("polymarket", "0.5", 100, category="Astrology (moons)", at=AFTER, rates=RATES)

    def test_at_only_on_polymarket(self) -> None:
        with self.assertRaisesRegex(ValueError, "at only applies"):
            fee("kalshi", "0.5", 1, at=AFTER, rates=RATES)
        with self.assertRaisesRegex(ValueError, "at only applies"):
            fill_fees("kalshi", [("0.5", 1)], at=AFTER, rates=RATES)

    def test_fills_on_both_sides_of_the_change(self) -> None:
        f = fill_fees(
            "polymarket",
            [
                {"price": "0.25", "contracts": 100, "at": BEFORE},
                {"price": "0.25", "contracts": 100, "at": AFTER},
            ],
            category="Crypto",
            rates=RATES,
        )
        self.assertEqual([x.coefficient for x in f.fills], [D("0.07"), D("0.01")])
        self.assertEqual(f.fee, D("1.31250") + D("0.18750"))
        # The at= argument is the default for fills without their own time.
        g = fill_fees("polymarket", [("0.25", 100)], category="Crypto", at=AFTER, rates=RATES)
        self.assertEqual(g.fee, D("0.18750"))

    def test_break_even_uses_one_period(self) -> None:
        from pmfees import break_even

        b = break_even("polymarket", "0.50", 100, category="Crypto", at=AFTER, rates=RATES)
        self.assertEqual(b.entry_fee, D("0.25000"))


class RebateTest(unittest.TestCase):
    def test_source_example_tier_4_maker(self) -> None:
        # "a Tier 4 maker whose eligible fills generate $100 in taker fees earns $15 in maker rebates"
        r = polymarket_rebate("maker", 100, tier="Tier 4", at=AFTER, rates=RATES)
        self.assertEqual(r.rebate, D(15))
        self.assertEqual(r.rate, D("0.15"))
        by_volume = polymarket_rebate("maker", 100, weighted_volume_30d=10_000_000, at=AFTER, rates=RATES)
        self.assertEqual((by_volume.tier.name, by_volume.rebate), ("Tier 4", D(15)))

    def test_taker_tiers(self) -> None:
        cases = [
            ("0", "None", "0"),
            ("199999.99", "None", "0"),
            ("200000", "Gold", "0.10"),
            ("999999.99", "Gold", "0.10"),
            ("1000000", "Platinum", "0.25"),
            ("4000000", "Diamond", "0.40"),
            ("10000000", "Obsidian", "0.50"),
            ("74999999.99", "Obsidian", "0.50"),
            ("75000000", "Onyx", "0.60"),
        ]
        for vol, name, rate in cases:
            with self.subTest(vol=vol):
                t = polymarket_rebate_tier("taker", vol, at=AFTER, rates=RATES)
                self.assertEqual((t.name, t.rebate), (name, D(rate)))
        self.assertEqual(polymarket_rebate("taker", 200, tier="Diamond", at=AFTER, rates=RATES).rebate, D(80))

    def test_maker_tiers(self) -> None:
        cases = [
            ("199999", "None", "0"),
            ("200000", "Tier 1", "0.03"),
            ("1000000", "Tier 2", "0.07"),
            ("4000000", "Tier 3", "0.10"),
            ("9999999", "Tier 3", "0.10"),
            ("10000000", "Tier 4", "0.15"),
            ("500000000", "Tier 4", "0.15"),
        ]
        for vol, name, rate in cases:
            with self.subTest(vol=vol):
                t = polymarket_rebate_tier("maker", vol, at=AFTER, rates=RATES)
                self.assertEqual((t.name, t.rebate), (name, D(rate)))

    def test_combos_and_fee_free_earn_no_maker_rebate(self) -> None:
        for cat in ("Combos", "Geopolitics"):
            r = polymarket_rebate("maker", 100, tier="Tier 4", category=cat, at=AFTER, rates=RATES)
            self.assertEqual(r.rebate, 0)

    def test_weighted_volume(self) -> None:
        # shares × price × (1 − price), not dollar volume (shares × price)
        self.assertEqual(weighted_volume([("0.40", 1000)]), D(240))
        self.assertEqual(
            weighted_volume([("0.40", 1000), {"price": "0.90", "contracts": 500, "maker": True}]),
            D(240) + D(45),
        )
        with self.assertRaises(ValueError):
            weighted_volume([("1.2", 10)])

    def test_not_modelled_before_the_change(self) -> None:
        with self.assertRaisesRegex(ValueError, "only modelled from 2026-11-20 23:59 UTC"):
            polymarket_rebate("maker", 100, tier="Tier 4", at=BEFORE, rates=RATES)
        with self.assertRaisesRegex(ValueError, "only modelled"):
            polymarket_rebate_tier("taker", 1_000_000, at=BEFORE, rates=RATES)

    def test_bad_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "either tier or weighted_volume_30d"):
            polymarket_rebate("maker", 100, at=AFTER, rates=RATES)
        with self.assertRaisesRegex(ValueError, "unknown maker rebate tier"):
            polymarket_rebate("maker", 100, tier="Gold", at=AFTER, rates=RATES)
        with self.assertRaisesRegex(ValueError, "role"):
            polymarket_rebate("both", 100, tier="Gold", at=AFTER, rates=RATES)  # type: ignore[arg-type]


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_rebate_command(self) -> None:
        code, out = self.run_cli(
            "polymarket-rebate",
            "--role",
            "maker",
            "--taker-fees",
            "100",
            "--tier",
            "Tier 4",
            "--at",
            "2026-11-21T08:00Z",
        )
        self.assertEqual(code, 0)
        self.assertIn("polymarket.com maker rebate: $15.00", out)

    def test_fee_command_at(self) -> None:
        code, out = self.run_cli(
            "polymarket",
            "--price",
            "0.25",
            "--contracts",
            "100",
            "--category",
            "Crypto",
            "--at",
            "2026-11-21T08:00Z",
        )
        self.assertEqual(code, 0)
        self.assertIn("polymarket.com taker fee: $0.1875", out)
        self.assertIn("rate period: from 2026-11-20 23:59 UTC", out)


if __name__ == "__main__":
    unittest.main()
