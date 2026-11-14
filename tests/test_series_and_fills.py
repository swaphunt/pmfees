"""Per-series Kalshi fee structure (kalshi_series.json) and per-fill fees (fill_fees)."""

from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from pmfees import (
    KalshiSeries,
    RatesError,
    break_even,
    default_kalshi_series,
    fee,
    fill_fees,
    kalshi_series,
    load_kalshi_series,
    load_rates,
)
from pmfees.cli import main

D = Decimal


class SeriesFileTest(unittest.TestCase):
    def test_source_links_and_dates(self) -> None:
        t = default_kalshi_series()
        rates = load_rates().kalshi
        self.assertRegex(t.as_of, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(t.source_url, "https://external-api.kalshi.com/trade-api/v2/series")
        self.assertEqual(t.fee_schedule_url, rates.source_url)
        self.assertEqual(t.fee_schedule_version, rates.version)
        self.assertEqual(t.fee_schedule_date, rates.source_date)
        self.assertGreater(t.series_total, len(t.series))

    def test_only_non_standard_series_are_listed(self) -> None:
        t = default_kalshi_series()
        self.assertTrue(t.series)
        for s in t.series.values():
            with self.subTest(series=s.ticker):
                self.assertNotEqual((s.fee_type, s.multiplier), (t.default_fee_type, t.default_multiplier))

    def test_multipliers_are_ones_the_rates_file_knows(self) -> None:
        allowed = set(load_rates().kalshi.multipliers)
        for s in default_kalshi_series().series.values():
            self.assertIn(s.multiplier, allowed, s.ticker)

    def test_maker_share_matches_fee_schedule_maker_rate(self) -> None:
        # Kalshi OpenAPI: maker fees are 0.25 × the taker formula; the fee schedule says 0.0175.
        k = load_rates().kalshi
        shares = default_kalshi_series().maker_shares
        self.assertEqual(k.taker_rate * shares["quadratic_with_maker_fees"], k.maker_rate)
        self.assertEqual(shares["quadratic"], 0)
        self.assertEqual(shares["quadratic_with_combo_maker_fees"], D("0.5"))

    def test_known_series(self) -> None:
        # From Get Series List on 2026-11-14.
        cases = {
            "KXMLBGAME": ("quadratic_with_maker_fees", D("0.5")),
            "KXMLBF5": ("quadratic", D("0.5")),
            "KXTRUMPOUT": ("quadratic", D("0")),
            "KXMVECROSSCATEGORY": ("quadratic_with_combo_maker_fees", D("1")),
        }
        for ticker, (fee_type, m) in cases.items():
            with self.subTest(ticker=ticker):
                s = kalshi_series(ticker)
                self.assertTrue(s.listed)
                self.assertEqual((s.fee_type, s.multiplier), (fee_type, m))

    def test_event_and_market_tickers_resolve_to_their_series(self) -> None:
        self.assertEqual(kalshi_series("kxmlbgame-26nov14nyybos-nyy").ticker, "KXMLBGAME")
        self.assertEqual(kalshi_series("KXMLBGAME-26NOV14NYYBOS").ticker, "KXMLBGAME")
        # A listed ticker with a hyphen is matched whole, not cut at the hyphen.
        self.assertEqual(kalshi_series("KXMVECROSSCATEGORY-SHARD1-X").ticker, "KXMVECROSSCATEGORY-SHARD1")
        # Hyphen boundaries only: KXMLBF5SPREAD is its own series, not KXMLBF5.
        self.assertEqual(kalshi_series("KXMLBF5SPREAD").ticker, "KXMLBF5SPREAD")

    def test_unlisted_series_is_standard(self) -> None:
        s = kalshi_series("KXHIGHNY-26NOV14")
        self.assertFalse(s.listed)
        self.assertEqual((s.fee_type, s.multiplier, s.maker_share), ("quadratic", D(1), D(0)))
        with self.assertRaises(ValueError):
            kalshi_series("  ")

    def test_load_custom_file_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "series.json"
            data = json.loads(
                (Path(__file__).resolve().parents[1] / "src/pmfees/data/kalshi_series.json").read_text()
            )
            data["series"] = {"KXTEST": {"fee_type": "quadratic", "multiplier": 0.5}}
            path.write_text(json.dumps(data))
            t = load_kalshi_series(path)
            self.assertEqual(t.lookup("KXTEST-1").multiplier, D("0.5"))
            data["series"] = {"KXFLAT": {"fee_type": "flat", "multiplier": 1}}
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(RatesError, "flat"):
                load_kalshi_series(path)
            path.write_text("[]")
            with self.assertRaises(RatesError):
                load_kalshi_series(path)


class SeriesFeeTest(unittest.TestCase):
    def test_taker_uses_series_multiplier(self) -> None:
        # 0.5 × 0.07 × 100 × 0.25 = 0.875
        r = fee("kalshi", "0.50", 100, series="KXMLBGAME")
        self.assertEqual(r.fee, D("0.875"))
        self.assertEqual(r.details["series"], "KXMLBGAME")
        self.assertEqual(r.details["series_fee_type"], "quadratic_with_maker_fees")
        self.assertEqual(r.details["multiplier"], "0.5")
        self.assertEqual(r.fee, fee("kalshi", "0.50", 100, multiplier="0.5").fee)

    def test_zero_multiplier_series_is_free(self) -> None:
        self.assertEqual(fee("kalshi", "0.40", 10, series="KXTRUMPOUT").fee, 0)

    def test_maker_fees_follow_fee_type(self) -> None:
        # quadratic: makers pay nothing, whatever M is.
        r = fee("kalshi", "0.50", 100, maker=True, series="KXMLBF5")
        self.assertEqual(r.fee, 0)
        self.assertIn("no maker fees", " ".join(r.notes))
        # quadratic_with_maker_fees, M = 0.5: 0.5 × 0.0175 × 100 × 0.25 = 0.21875 -> 0.2188
        self.assertEqual(fee("kalshi", "0.50", 100, maker=True, series="KXMLBGAME").fee, D("0.2188"))
        # quadratic_with_combo_maker_fees, M = 1: 0.5 × 0.07 × 100 × 0.25 = 0.875
        self.assertEqual(fee("kalshi", "0.50", 100, maker=True, series="KXMVECROSSCATEGORY").fee, D("0.875"))
        # Same as the explicit multiplier for a standard maker-fee series.
        self.assertEqual(
            fee("kalshi", "0.50", 100, maker=True, series="KXA100MON").fee,
            fee("kalshi", "0.50", 100, maker=True, multiplier=1).fee,
        )

    def test_unlisted_series_gets_a_note(self) -> None:
        r = fee("kalshi", "0.50", 100, series="KXHIGHNY")
        self.assertEqual(r.fee, D("1.75"))
        self.assertIn("standard structure", r.notes[0])
        self.assertIn("not listed", r.details["series_fee_type"])

    def test_series_object(self) -> None:
        s = KalshiSeries("KXX", "quadratic", D("0.5"), D(0))
        self.assertEqual(fee("kalshi", "0.50", 100, series=s).fee, D("0.875"))

    def test_invalid_combinations(self) -> None:
        with self.assertRaisesRegex(ValueError, "either series or multiplier"):
            fee("kalshi", "0.5", 1, series="KXMLBGAME", multiplier=1)
        with self.assertRaisesRegex(ValueError, "series only applies to kalshi"):
            fee("polymarket-us", "0.5", 1, series="KXMLBGAME")

    def test_break_even_with_series(self) -> None:
        be = break_even("kalshi", "0.50", 100, series="KXMLBF5")
        self.assertEqual(be.entry_fee, D("0.875"))

    def test_cli(self) -> None:
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["kalshi", "--price", "0.50", "--contracts", "100", "--series", "KXMLBGAME"]), 0
            )
        self.assertIn("Kalshi taker fee: $0.875", out.getvalue())
        self.assertIn("series: KXMLBGAME", out.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            main(["kalshi", "--price", "0.5", "--contracts", "1", "--series", "X", "--multiplier", "1"])


class FillFeesTest(unittest.TestCase):
    def test_each_fill_rounded_up_on_its_own(self) -> None:
        # 1 @ 0.33: 0.07 × 0.33 × 0.67 = 0.015477 -> 0.0155 per fill; 5 fills 0.0775.
        r = fill_fees("kalshi", [("0.33", 1)] * 5)
        self.assertEqual(r.fees, (D("0.0155"),) * 5)
        self.assertEqual(r.fee, D("0.0775"))
        self.assertEqual(r.raw_fee, D("0.077385"))
        self.assertEqual(r.contracts, 5)
        self.assertEqual(r.trade_value, D("1.65"))
        self.assertEqual(r.cash_flow, D("-1.7275"))
        # One fill of 5 rounds once: 0.077385 -> 0.0774.
        self.assertEqual(fee("kalshi", "0.33", 5).fee, D("0.0774"))

    def test_sum_matches_fee_per_fill(self) -> None:
        fills = [("0.33", 7), ("0.34", 3), ("0.35", 10)]
        r = fill_fees("kalshi", fills)
        self.assertEqual(r.fee, sum((fee("kalshi", p, c).fee for p, c in fills), D(0)))
        self.assertEqual([f.price for f in r.fills], [D("0.33"), D("0.34"), D("0.35")])

    def test_same_order_accumulator_refunds_rounding(self) -> None:
        # Rounding of 0.000023 per fill builds up; fill 5 gets a 0.0001 rebate.
        r = fill_fees("kalshi", [("0.33", 1)] * 5, same_order=True)
        self.assertEqual(r.rebates, (D(0), D(0), D(0), D(0), D("0.0001")))
        self.assertEqual(r.fee, D("0.0774"))
        self.assertEqual(r.unrebated_rounding, D("0.000015"))
        self.assertEqual(r.fee, fee("kalshi", "0.33", 5).fee)

    def test_same_order_converges_to_one_fill(self) -> None:
        # Kalshi: "the total fee converges to what a single equivalent fill would cost".
        for price in ("0.07", "0.33", "0.5", "0.61", "0.97"):
            for split in ((1,) * 9, (2, 3, 4), (5, 1, 1, 1, 1), (8, 1)):
                with self.subTest(price=price, split=split):
                    r = fill_fees("kalshi", [(price, c) for c in split], same_order=True)
                    single = fee("kalshi", price, sum(split)).fee
                    self.assertLess(abs(r.fee - single), D("0.0001"))
                    self.assertLessEqual(r.fee, fill_fees("kalshi", [(price, c) for c in split]).fee)
                    self.assertTrue(all(f >= 0 for f in r.fees))

    def test_rebate_never_makes_a_fill_negative(self) -> None:
        # Maker fills on a no-maker-fee series cost 0, so there is nothing to rebate against.
        fills = [{"price": "0.33", "contracts": 1}] * 5 + [{"price": "0.5", "contracts": 1, "maker": True}]
        r = fill_fees("kalshi", fills, same_order=True)
        self.assertEqual(r.fills[-1].role, "maker")
        self.assertEqual(r.fees[-1], 0)

    def test_mixed_taker_and_maker_fills_with_series(self) -> None:
        fills = [{"price": "0.5", "contracts": 100}, {"price": "0.5", "contracts": 100, "maker": True}]
        r = fill_fees("kalshi", fills, series="KXMLBGAME")
        self.assertEqual(r.fees, (D("0.875"), D("0.2188")))

    def test_other_platforms_round_each_fill(self) -> None:
        # Polymarket US rounds each fill to the cent: 0.095 × 1 × 0.25 = 0.02375 -> 0.02.
        r = fill_fees("polymarket-us", [("0.5", 1)] * 4)
        self.assertEqual(r.fee, D("0.08"))
        self.assertEqual(fee("polymarket-us", "0.5", 4).fee, D("0.10"))
        with self.assertRaisesRegex(ValueError, "same_order only applies to kalshi"):
            fill_fees("polymarket-us", [("0.5", 1)], same_order=True)
        g = fill_fees("polymarket", [("0.25", 10), ("0.26", 5)], category="Crypto")
        self.assertEqual(g.platform_name, "polymarket.com")

    def test_breakdown_is_json_ready(self) -> None:
        b = fill_fees("kalshi", [("0.33", 1)] * 2, same_order=True).breakdown()
        json.dumps(b)
        self.assertEqual(b["fee_usd"], "0.0310")
        self.assertIsInstance(b["fills"], list)

    def test_bad_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            fill_fees("kalshi", [])
        with self.assertRaisesRegex(ValueError, "fill 1: contracts"):
            fill_fees("kalshi", [("0.5", 1), ("0.5", "1.5")])
        with self.assertRaisesRegex(ValueError, "fill 0: needs"):
            fill_fees("kalshi", [{"price": "0.5"}])
        with self.assertRaisesRegex(ValueError, "fill 0: expected"):
            fill_fees("kalshi", [("0.5",)])  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
