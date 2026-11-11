"""The README's date and worked examples must match the code and the bundled rates."""

from __future__ import annotations

import contextlib
import io
import re
import unittest
from decimal import Decimal
from pathlib import Path

from pmfees import fee, load_rates
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
        self.assertIn(
            f"Rates current as of: Kalshi {r.kalshi.as_of} (fee schedule {r.kalshi.version}), "
            f"Polymarket US {r.polymarket_us.as_of},\npolymarket.com {r.polymarket_global.as_of}.",
            self.text,
        )
        for platform in (r.kalshi, r.polymarket_us, r.polymarket_global):
            self.assertIn(f"| {platform.as_of} |", self.text)

    def test_cli_examples(self) -> None:
        examples = [
            ("kalshi --price 0.50 --contracts 100", "Kalshi taker fee: $1.75 (3.50% of trade value at 50¢)"),
            (
                "polymarket-us --price 0.50 --contracts 100",
                "Polymarket US taker fee: $2.38 (4.76% of trade value at 50¢)",
            ),
            (
                "polymarket --price 0.25 --contracts 100 --category Crypto",
                "polymarket.com taker fee: $1.3125 (5.25% of trade value at 25¢)",
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

    def test_says_platforms_not_exchanges(self) -> None:
        self.assertNotIn("exchange", self.text.lower())


if __name__ == "__main__":
    unittest.main()
