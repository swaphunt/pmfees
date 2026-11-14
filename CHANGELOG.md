# Changelog

## 0.2.0 (2026-11-14)

- `fill_fees()`: the fee for a list of fills. By default (`same_order=True`) the fills are one
  Kalshi order: each fill is rounded up to a whole centicent, and Kalshi's per-order fee accumulator
  refunds that rounding in centicent steps, so the total converges to one equivalent fill
  ([Fee Rounding](https://docs.kalshi.com/getting_started/fee_rounding.md)). `same_order=False`
  rounds each fill on its own with no refund, for fills from different orders. Polymarket US and
  polymarket.com always round each fill on its own. Returns `FillFees` with per-fill results,
  rebates and a JSON-ready `breakdown()`.
- Kalshi per-series fees: `fee(..., series="KXMLBGAME")`, `break_even(..., series=...)` and
  `pmfees kalshi --series`. M and maker fees come from `src/pmfees/data/kalshi_series.json`, built
  from Kalshi's Get Series List (`fee_type`, `fee_multiplier`) on 2026-11-14: the 195 of 16,442
  series that differ from the standard. Event and market tickers resolve to their series; unlisted
  tickers are standard, with a note. `fee_type` follows the fee schedule (7.7.26, 2026-07-07):
  makers pay 0.25 × the taker fee on `quadratic_with_maker_fees`, 0.5 × on
  `quadratic_with_combo_maker_fees`, nothing on `quadratic`.
- `kalshi_series()`, `load_kalshi_series()` (or `PMFEES_KALSHI_SERIES_FILE`) and
  `scripts/update_kalshi_series.py` to rebuild or `--check` the series file.

## 0.1.1 (2026-11-11)

- `contracts` must match the platform's order size step: whole contracts on Kalshi, 0.01 on
  Polymarket US, at most 6 decimals on polymarket.com. Other sizes raise `ValueError`
  (0.1.0 computed a fee for e.g. 1.5 Kalshi contracts).
- README: limitations (Polymarket US futures that trade whole contracts only are not detected).

## 0.1.0 (2026-11-11)

First release.

- `fee()` and `break_even()` for Kalshi, Polymarket US and polymarket.com, with each platform's own
  formula and rounding (Kalshi: fee + position cost rounded up to a whole centicent).
- `pmfees` command line tool with `--json` and `--break-even`.
- Rates in `src/pmfees/data/prediction_market_fees.json`, with `as_of` and `source_url` per platform;
  results carry the platform's `rates_as_of`, and Kalshi results the fee schedule version.
