"""The pmfees command line."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from pmfees.cli import main

FIXTURE = str(Path(__file__).parent / "fixtures" / "rates_2026-11-08.json")


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([*args, "--rates", FIXTURE])
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_kalshi_readme_example(self) -> None:
        code, out, _ = run("kalshi", "--price", "0.50", "--contracts", "100")
        self.assertEqual(code, 0)
        self.assertIn("Kalshi taker fee: $1.75 (3.50% of trade value at 50¢)", out)
        self.assertIn("rates as of 2026-11-08", out)

    def test_polymarket_us_json(self) -> None:
        code, out, _ = run("polymarket-us", "--price", "0.65", "--contracts", "1000", "--tier", "4", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["fee"]["fee_usd"], "15.81")

    def test_polymarket_break_even(self) -> None:
        code, out, _ = run(
            "polymarket", "--price", "0.25", "--contracts", "100", "--category", "Crypto", "--break-even"
        )
        self.assertEqual(code, 0)
        self.assertIn("polymarket.com taker fee: $1.3125", out)
        self.assertIn("Break-even: the price must rise", out)

    def test_error_exit_code(self) -> None:
        code, _, err = run("polymarket", "--price", "0.5", "--contracts", "1", "--category", "Astrology")
        self.assertEqual(code, 2)
        self.assertIn("unknown polymarket.com category", err)

    def test_rates_command(self) -> None:
        code, out, _ = run("rates")
        self.assertEqual(code, 0)
        self.assertIn("Rates current as of 2026-11-08", out)


if __name__ == "__main__":
    unittest.main()
