"""The README's date and worked examples must match the code and the bundled rates."""

from __future__ import annotations

import contextlib
import io
import re
import unittest
from decimal import Decimal
from pathlib import Path

from pmfees import fee, fill_fees, load_rates, polymarket_rebate, polymarket_rebate_tier, weighted_volume
from pmfees.cli import main

README = Path(__file__).resolve().parents[1] / "README.md"


def cli(*args: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(list(args))
    return out.getvalue()


@unittest.skipUnless(README.exists(), "README not shipped with the sdist tests")
class ReadmeTest(unittest.TestCase):
    text = README.read_text(encoding="utf-8") if README.exists() else ""

    def test_rates_dates_match_bundled_file(self) -> None:
        r = load_rates()
        g = r.polymarket_global
        # polymarket.com: the headline gives the latest dated period (the rates in force now) and
        # the date of the rates before it, so it does not go stale after a scheduled change.
        latest = g.schedule[-1] if g.schedule else None
        pm = (
            f"polymarket.com {latest.as_of} (in force from {latest.effective:%Y-%m-%d %H:%M} UTC; earlier "
            f"fills use the rates as of\n{g.as_of})"
            if latest
            else f"polymarket.com {g.as_of}"
        )
        self.assertIn(
            f"Rates current as of: Kalshi {r.kalshi.as_of} (fee schedule {r.kalshi.version}), "
            f"Polymarket US {r.polymarket_us.as_of},\n{pm}.",
            self.text,
        )
        for as_of in (r.kalshi.as_of, r.polymarket_us.as_of, g.as_of, *(p.as_of for p in g.schedule)):
            self.assertIn(f"| {as_of} |", self.text)

    def test_cli_examples(self) -> None:
        examples = [
            ("kalshi --price 0.50 --contracts 100", "Kalshi taker fee: $1.75 (3.50% of trade value at 50¢)"),
            (
                "polymarket-us --price 0.50 --contracts 100",
                "Polymarket US taker fee: $2.38 (4.76% of trade value at 50¢)",
            ),
            (
                "polymarket --price 0.25 --contracts 100 --category Crypto --at 2026-11-20T12:00Z",
                "polymarket.com taker fee: $1.3125 (5.25% of trade value at 25¢)",
            ),
            (
                "polymarket --price 0.25 --contracts 100 --category Crypto --at 2026-11-21T00:00Z",
                "polymarket.com taker fee: $0.1875 (0.75% of trade value at 25¢)",
            ),
            (
                "polymarket-rebate --role maker --taker-fees 100 --tier Tier4 --at 2026-11-21T00:00Z",
                "polymarket.com maker rebate: $15.00",
            ),
            (
                "kalshi --price 0.25 --contracts 100 --multiplier 0.5",
                "Kalshi taker fee: $0.6563 (2.63% of trade value at 25¢)",
            ),
            (
                "kalshi --price 0.50 --contracts 100 --break-even",
                "Break-even: the price must rise 4¢ to 54¢ (tick 1¢); entry fee $1.75 + exit fee $1.7388, "
                "profit at that price $0.5112",
            ),
        ]
        for args, line in examples:
            with self.subTest(args=args):
                self.assertIn(f"pmfees {args}", self.text)
                self.assertIn(line, self.text)
                self.assertIn(line, cli(*args.split()))

    def test_library_examples(self) -> None:
        """Every `fee(...).fee  # Decimal('x')` line in the README runs and gives x."""
        pattern = re.compile(r"^(fee\(.*\)\.fee)\s+# Decimal\('([0-9.]+)'\)", re.M)
        found = pattern.findall(self.text)
        self.assertGreaterEqual(len(found), 10)
        for expr, expected in found:
            with self.subTest(expr=expr):
                self.assertEqual(eval(expr, {"fee": fee}), Decimal(expected))

    def test_fill_fees_examples(self) -> None:
        """Every `fill_fees(...).fee  # Decimal('x')` line in the README runs and gives x."""
        pattern = re.compile(r"^(fill_fees\(.*\)\.fee)\s+# Decimal\('([0-9.]+)'\)", re.M)
        found = pattern.findall(self.text)
        self.assertGreaterEqual(len(found), 3)
        env = {"fee": fee, "fill_fees": fill_fees, "fills": [(0.33, 1)] * 5}
        for expr, expected in found:
            with self.subTest(expr=expr):
                self.assertEqual(eval(expr, env), Decimal(expected))

    def test_polymarket_rebate_examples(self) -> None:
        """The rebate and weighted-volume lines in the README run and give what they say."""
        env = {
            "polymarket_rebate": polymarket_rebate,
            "polymarket_rebate_tier": polymarket_rebate_tier,
            "weighted_volume": weighted_volume,
            "t": "2026-11-21T00:00Z",
        }
        pattern = re.compile(
            r"^((?:polymarket_rebate|weighted_volume)\(.*\)(?:\.rebate)?)\s+# Decimal\('([0-9.]+)'\)", re.M
        )
        found = pattern.findall(self.text)
        self.assertGreaterEqual(len(found), 2)
        for expr, expected in found:
            with self.subTest(expr=expr):
                self.assertEqual(eval(expr, env), Decimal(expected))
        self.assertIn('r = polymarket_rebate("maker", 100, tier="Tier 4", at="2026-11-21T00:00Z")', self.text)
        r = polymarket_rebate("maker", 100, tier="Tier 4", at="2026-11-21T00:00Z")
        self.assertEqual(r.rebate, Decimal("15.00"))
        tier_line = 'polymarket_rebate_tier("taker", 1_500_000, at="2026-11-21T00:00Z").name  # \'Platinum\''
        self.assertIn(tier_line, self.text)
        self.assertEqual(polymarket_rebate_tier("taker", 1_500_000, at="2026-11-21T00:00Z").name, "Platinum")
        self.assertIn("https://docs.polymarket.com/programs/fee-and-rebate-changes-november-2026", self.text)

    def test_quotes_kalshi_fee_rounding_page(self) -> None:
        self.assertIn("https://docs.kalshi.com/getting_started/fee_rounding.md", self.text)
        quote = (
            "The **fee accumulator** applies across all fills of an order so that the total fee "
            "converges to what a single equivalent fill would cost."
        )
        quoted = " ".join(
            line.removeprefix("> ").strip() for line in self.text.splitlines() if line.startswith("> ")
        )
        self.assertIn(quote, quoted)

    def test_series_cli_example(self) -> None:
        args = "kalshi --price 0.50 --contracts 100 --series KXMLBGAME"
        line = "Kalshi taker fee: $0.875 (1.75% of trade value at 50¢)"
        self.assertIn(f"pmfees {args}", self.text)
        self.assertIn(line, self.text)
        self.assertIn(line, cli(*args.split()))

    def test_says_platforms_not_exchanges(self) -> None:
        self.assertNotIn("exchange", self.text.lower())


if __name__ == "__main__":
    unittest.main()
