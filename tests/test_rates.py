"""The rates file: bundled copy, overrides and validation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock

from pmfees import RatesError, fee, load_rates
from pmfees.rates import ENV_VAR

FIXTURE = Path(__file__).parent / "fixtures" / "rates_2026-11-08.json"
BUNDLED = Path(__file__).resolve().parents[1] / "src" / "pmfees" / "data" / "prediction_market_fees.json"


def _fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


class BundledRatesTest(unittest.TestCase):
    def test_bundled_file_loads_and_every_option_works(self) -> None:
        rates = load_rates()
        self.assertRegex(rates.as_of, r"^\d{4}-\d{2}-\d{2}$")
        for cat in rates.polymarket_global.rates:
            fee("polymarket", "0.5", 100, category=cat, rates=rates)
        for t in rates.polymarket_us.tiers:
            fee("polymarket-us", "0.5", 100, tier=t.number, rates=rates)
        fee("polymarket-us", "0.5", 100, tier="new", rates=rates)
        for m in rates.kalshi.multipliers:
            fee("kalshi", "0.5", 100, multiplier=m, rates=rates)
            fee("kalshi", "0.5", 100, maker=True, multiplier=m, rates=rates)

    def test_bundled_file_is_a_plain_file_with_sources_and_dates(self) -> None:
        self.assertFalse(BUNDLED.is_symlink())
        data = json.loads(BUNDLED.read_text(encoding="utf-8"))
        self.assertFalse([k for k in data if k.startswith("_")], "no internal fields in the public file")
        for key in ("polymarket_global", "polymarket_us", "kalshi"):
            self.assertRegex(data[key]["as_of"], r"^\d{4}-\d{2}-\d{2}$", key)
            self.assertTrue(data[key]["source_url"].startswith("https://"), key)
        rates = load_rates()
        self.assertEqual(rates.kalshi.as_of, data["kalshi"]["as_of"])
        self.assertEqual(rates.polymarket_us.as_of, data["polymarket_us"]["as_of"])

    def test_tiers_ordered_by_volume(self) -> None:
        tiers = load_rates().polymarket_us.tiers
        bounds = [t.below for t in tiers[:-1]]
        self.assertEqual(bounds, sorted(b for b in bounds if b is not None))


class OverrideTest(unittest.TestCase):
    def _write(self, data: dict[str, Any]) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.addCleanup(os.unlink, path)
        return path

    def test_updated_file_changes_the_fee(self) -> None:
        data = _fixture()
        data["as_of"] = "2027-01-01"
        data["kalshi"]["taker_rate"] = 0.05
        rates = load_rates(self._write(data))
        r = fee("kalshi", "0.50", 100, rates=rates)
        self.assertEqual(r.fee, Decimal("1.25"))
        self.assertEqual(r.rates_as_of, "2027-01-01")

    def test_env_var(self) -> None:
        data = _fixture()
        data["as_of"] = "2030-02-02"
        path = self._write(data)
        with mock.patch.dict(os.environ, {ENV_VAR: path}):
            self.assertEqual(load_rates().as_of, "2030-02-02")

    def test_missing_field_is_a_clear_error(self) -> None:
        data = _fixture()
        del data["kalshi"]["maker_rate"]
        with self.assertRaisesRegex(RatesError, "maker_rate"):
            load_rates(self._write(data))

    def test_unknown_rounding_mode(self) -> None:
        data = _fixture()
        data["polymarket_us"]["rounding"]["mode"] = "sideways"
        with self.assertRaisesRegex(RatesError, "rounding mode"):
            load_rates(self._write(data))


if __name__ == "__main__":
    unittest.main()


class KalshiRoundingFromFile(unittest.TestCase):
    """Kalshi's rounding comes from kalshi.rounding in the rates file, not from the code."""

    FIXTURE = Path(__file__).parent / "fixtures" / "rates_2026-11-08.json"

    def _rates(self, **rounding: Any) -> Any:
        data = json.loads(self.FIXTURE.read_text())
        data["kalshi"]["rounding"].update(rounding)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
        self.addCleanup(os.unlink, f.name)
        return load_rates(f.name)

    def test_centicent_on_fee_plus_position_cost(self) -> None:
        r = fee("kalshi", "0.25", 100, rates=self._rates())
        self.assertEqual(r.fee, Decimal("1.3125"))
        self.assertIn("fee + position cost", r.rounding)

    def test_file_rule_is_used(self) -> None:
        cent = self._rates(decimals=2, basis=None)
        self.assertEqual(fee("kalshi", "0.25", 100, rates=cent).fee, Decimal("1.32"))

    def test_unknown_basis_is_rejected(self) -> None:
        with self.assertRaises(RatesError):
            self._rates(basis="per_contract")
