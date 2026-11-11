# Changelog

## 0.1.0 (2026-11-11)

First release.

- `fee()` and `break_even()` for Kalshi, Polymarket US and polymarket.com, with each platform's own
  formula and rounding (Kalshi: fee + position cost rounded up to a whole centicent).
- `pmfees` command line tool with `--json` and `--break-even`.
- Rates in `src/pmfees/data/prediction_market_fees.json`, with `as_of` and `source_url` per platform;
  results carry the platform's `rates_as_of`, and Kalshi results the fee schedule version.
