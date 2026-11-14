# pmfees

Exact trading fees for the prediction market platforms **Kalshi**, **Polymarket US** and
**polymarket.com** (international), as a small Python library and a command line tool.

- The platforms' own formulas and rounding rules: Kalshi rounds up so that fee + position cost
  is a whole centicent ($0.0001), Polymarket US rounds to the cent with banker's rounding,
  polymarket.com rounds to 5 decimals.
- Exact decimal arithmetic (`decimal.Decimal`), so $1.75 is $1.75 and not $1.7500000000000002.
- A break-even helper: how far the price has to move before a round trip is in profit after
  the fee on both orders.
- Fees for a list of fills, each fill rounded on its own and then summed, with Kalshi's per-order
  rounding refund as an option.
- Kalshi's per-series fee structure (multiplier M and maker fees) for the 195 series that differ
  from the standard, looked up by series, event or market ticker.
- No dependencies outside the Python standard library. Typed (`py.typed`).

Rates current as of: Kalshi 2026-11-10 (fee schedule 7.7.26), Polymarket US 2026-11-09,
polymarket.com 2026-11-08. Each platform's date and source are in the rates file and in every result.
Kalshi series list as of 2026-11-14.

Other ways to get the same numbers, from the same rates file:

- In a browser: the [SwapHunt Kalshi vs Polymarket fee calculator](https://swaphunt.dev/tools/kalshi-vs-polymarket-fee-calculator).
- Over HTTP, for agents: [`GET https://x402.swaphunt.dev/v1/pm/fees`](https://swaphunt.dev/api), paid per call
  with [x402](https://www.x402.org/) in USDC. This package is free; the endpoint exists for agents that
  cannot install a library.

## Install

```bash
pip install pmfees
```

Python 3.10 or newer.

## Command line

```bash
pmfees kalshi --price 0.50 --contracts 100
```

```text
Kalshi taker fee: $1.75 (3.50% of trade value at 50¢)
  buy 100 contracts at $0.50 = $50.00 trade value
  base rate: 0.07
  multiplier: 1
  fee schedule version: 7.7.26
  0.07 × 100 × 0.5 × (1 − 0.5) = 1.75, rounded up so that fee + position cost is a whole centicent ($0.0001): $1.75
  rates as of 2026-11-10 · source: https://kalshi.com/docs/kalshi-fee-schedule.pdf
```

More examples:

```bash
# Polymarket US, Tier 1 (under $10,000 of 30-day taker volume, the default)
pmfees polymarket-us --price 0.50 --contracts 100
# -> Polymarket US taker fee: $2.38 (4.76% of trade value at 50¢)   [0.095 × 100 × 0.5 × 0.5 = 2.375]

# polymarket.com, Crypto category
pmfees polymarket --price 0.25 --contracts 100 --category Crypto
# -> polymarket.com taker fee: $1.3125 (5.25% of trade value at 25¢)

# Break-even: buy 100 on Kalshi at 50¢, when is selling in profit?
pmfees kalshi --price 0.50 --contracts 100 --break-even
# -> Break-even: the price must rise 4¢ to 54¢ (tick 1¢); entry fee $1.75 + exit fee $1.7388, profit at that price $0.5112
```

Options:

| Option | Platforms | Meaning |
|---|---|---|
| `--price` | all | Price of the contract you trade, in dollars (0 < price < 1) |
| `--contracts` | all | Number of contracts (shares on polymarket.com): whole contracts on Kalshi, steps of 0.01 on Polymarket US, up to 6 decimals on polymarket.com |
| `--side buy\|sell` | all | Changes the cash flow only; the fee formulas are symmetric |
| `--maker` | all | Maker order. Kalshi: maker rate 0.0175 with M default 0, so pass `--multiplier 1` (or `--series`) on markets with maker fees. Polymarket US and polymarket.com: makers pay no fee |
| `--multiplier` | kalshi | Fee multiplier M (default 1 for takers, 0 for makers) |
| `--series` | kalshi | Series, event or market ticker (e.g. `KXMLBGAME`): M and maker fees from the series list. Instead of `--multiplier` |
| `--tier 1-4\|new` | polymarket-us | Taker tier, or `new` for the first 7 days of a new account (default 1) |
| `--volume` | polymarket-us | Trailing 30-day taker volume in USD; picks the tier for you |
| `--category` | polymarket | Market category, e.g. `Crypto`, `Sports`, `Politics` (required) |
| `--break-even`, `--tick` | all | Also print the break-even move (price step, default 0.01) |
| `--json` | all | Machine-readable output |
| `--rates PATH` | all | Use an updated rates file |

`pmfees rates` prints the loaded rates and their date.

## Library

```python
from pmfees import fee, break_even

r = fee("kalshi", 0.50, 100)
r.fee  # Decimal('1.7500')  what you pay, in USD
r.raw_fee  # Decimal('1.7500') before rounding
r.fee_pct_of_trade_value  # Decimal('3.500')
r.breakdown()  # dict of strings: coefficient, rounding, formula, source_url, rates_as_of, ...

fee("polymarket-us", 0.10, 1000, "buy", tier=4).fee  # Decimal('6.26')
fee("polymarket-us", 0.10, 1000, volume_30d=12_500).fee  # Tier 2
fee("polymarket", 0.25, 100, category="Crypto").fee  # Decimal('1.31250')
fee("kalshi", 0.50, 1).fee  # Decimal('0.0175')  centicent rounding, not $0.02
fee("kalshi", 0.50, 100, maker=True).fee  # Decimal('0.0000')  maker M defaults to 0
fee("kalshi", 0.50, 100, maker=True, multiplier=1).fee  # Decimal('0.4375')
fee("kalshi", 0.50, 100, multiplier=0.5).fee  # Decimal('0.8750')

b = break_even("polymarket-us", 0.40, 500, tier=2)
b.exit_price, b.move_cents  # (Decimal('0.45'), Decimal('5.00'))
```

Signature: `fee(platform, price, contracts, side="buy", tier=None, maker=False, *, category=None,
multiplier=None, series=None, volume_30d=None, rates=None) -> FeeResult`. Platform names: `kalshi`,
`polymarket-us`, `polymarket` (also `polymarket.com`). Prices and counts can be `str`, `int`,
`float` or `Decimal`; floats are converted through `str`, so `0.1` means exactly 0.1.

`break_even(...)` takes the same arguments plus `tick`. It opens the position at `price`, then
walks the price in `tick` steps (up for a buy, down for a sell) and returns the first exit price
where proceeds − cost − entry fee − exit fee > 0, or `exit_price=None` if no price before $1 (or
$0) works.

## Kalshi: centicent rounding and the multiplier

Kalshi does not round the fee on its own. It rounds **fee + position cost** (contracts × price) up to
a whole centicent ($0.0001), per the fee schedule (version 7.7.26, page 2). Most of the time that
looks like rounding the fee up to four decimals, but the fee alone is what you see change:

```python
from pmfees import fee

fee("kalshi", 0.50, 1).fee  # Decimal('0.0175')  0.07 × 1 × 0.5 × 0.5 = 0.0175 exactly; not $0.02
fee("kalshi", 0.33, 7).fee  # Decimal('0.1084')  raw 0.108339; 2.31 + 0.108339 = 2.418339 -> 2.4184
```

Accounts cleared through a broker (FCM) keep balances in whole cents, so there the fee rounds to the
cent ([Kalshi docs](https://docs.kalshi.com/getting_started/fee_rounding.md)). pmfees models
kalshi.com accounts (direct members).

Each Kalshi series has a fee multiplier M: 1 (standard), 0.5 or 0. Takers default to M = 1, makers
to M = 0, because Kalshi charges makers only on series listed with maker fees:

```python
fee("kalshi", 0.25, 100).fee  # Decimal('1.3125')  M = 1
fee("kalshi", 0.25, 100, multiplier=0.5).fee  # Decimal('0.6563')  raw 0.65625, rounded up
fee("kalshi", 0.50, 100, maker=True).fee  # Decimal('0.0000')  maker, M defaults to 0
fee("kalshi", 0.50, 100, maker=True, multiplier=1).fee  # Decimal('0.4375')  0.0175 × 100 × 0.25
```

```bash
pmfees kalshi --price 0.25 --contracts 100 --multiplier 0.5
# -> Kalshi taker fee: $0.6563 (2.63% of trade value at 25¢)
```

### Per-series fees

Kalshi publishes a `fee_type` and `fee_multiplier` for every series in its Trade API
([Get Series List](https://docs.kalshi.com/api-reference/market/get-series)). The `fee_type` points
to a table in the [fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) (version 7.7.26,
2026-07-07): `quadratic` (no maker fees), `quadratic_with_maker_fees` (makers pay 0.25 × the taker
fee, i.e. 0.0175) and `quadratic_with_combo_maker_fees` (makers pay 0.5 × the taker fee). On
2026-11-14, 16,247 of 16,442 series were standard (`quadratic`, M = 1). The other 195 are in
`src/pmfees/data/kalshi_series.json`, with that date and the source links. Some examples: MLB
first-innings and player-prop series have M = 0.5, a few long-dated political and economic series
have M = 0, and about 160 series charge makers.

Pass `series=` instead of `multiplier=`, with a series, event or market ticker:

```python
fee("kalshi", 0.50, 100, series="KXMLBF5").fee  # Decimal('0.8750')  M = 0.5
fee("kalshi", 0.50, 100, series="KXMLBGAME-26NOV14NYYBOS-NYY").fee  # Decimal('0.8750')  series KXMLBGAME
fee("kalshi", 0.50, 100, maker=True, series="KXMLBGAME").fee  # Decimal('0.2188')  0.5 × 0.0175 × 100 × 0.25
fee("kalshi", 0.50, 100, maker=True, series="KXMLBF5").fee  # Decimal('0.0000')  no maker fees
fee("kalshi", 0.50, 100, series="KXHIGHNY").fee  # Decimal('1.7500')  not listed, so standard
```

A ticker that is not in the file gets the standard structure, with a note in `FeeResult.notes`.
`kalshi_series("KXMLBGAME")` returns the entry itself. Kalshi can change a series' fees at any time;
`python3 scripts/update_kalshi_series.py` rebuilds the file from the API (`--check` only compares),
and `load_kalshi_series(path)` or `PMFEES_KALSHI_SERIES_FILE` reads an updated copy.

```bash
pmfees kalshi --price 0.50 --contracts 100 --series KXMLBGAME
# -> Kalshi taker fee: $0.875 (1.75% of trade value at 50¢)
```

## Fees per fill

An order that fills at several prices pays a fee on every fill, and each fill is rounded on its own.
`fill_fees()` prices each fill with `fee()` and adds them up. Fills are `(price, contracts)` pairs or
dicts with `price`, `contracts` and optionally `maker`; the other arguments are the same as `fee()`:

```python
from pmfees import fill_fees

fills = [(0.33, 1)] * 5
r = fill_fees("kalshi", fills)
r.fees  # (Decimal('0.0155'),) * 5: each fill 0.015477, rounded up to a centicent by itself
fill_fees("kalshi", fills).fee  # Decimal('0.0775')
fee("kalshi", 0.33, 5).fee  # Decimal('0.0774')  one fill of 5 rounds once
fill_fees("kalshi", [(0.33, 7), (0.34, 3)], series="KXMLBGAME").fee  # Decimal('0.0778')  0.0542 + 0.0236
```

Within one order, Kalshi gives the extra rounding back: a fee accumulator collects each fill's
rounding and refunds it in centicent steps, so the order's total converges to what one fill of the
same size would cost ([Fee Rounding](https://docs.kalshi.com/getting_started/fee_rounding.md)).
Pass `same_order=True` when the fills belong to one order:

```python
r = fill_fees("kalshi", fills, same_order=True)
r.rebates  # 0.0001 refunded on the fifth fill
fill_fees("kalshi", fills, same_order=True).fee  # Decimal('0.0774')
```

Without `same_order` (the default), the fills are treated as separate orders. `r.breakdown()` gives
every fill's raw fee, rounded fee, rebate and net fee as strings, ready for JSON.

## The formulas

| Platform | Taker fee | Rounding |
|---|---|---|
| Kalshi | M × 0.07 × C × P × (1 − P), M default 1; maker M × 0.0175 × C × P × (1 − P), M default 0 | Up, so that fee + position cost is a whole centicent ($0.0001) |
| Polymarket US | Θ × C × p × (1 − p), Θ by 30-day taker volume: 0.095 (< $10K), 0.085 (< $50K), 0.075 (< $250K), 0.0695 ($250K+); new accounts 0.0695 for the first 7 days | Nearest cent, half to even |
| polymarket.com | C × rate × p × (1 − p), rate by category (Crypto 0.07; Sports, Economics, Culture, Weather, Other, Combos 0.05; Finance, Politics, Mentions, Tech 0.04; Geopolitics 0) | 5 decimals |

The numbers in this table are for reading only. The code takes every rate, tier, multiplier and
rounding rule (including Kalshi's fee + position cost basis) from `prediction_market_fees.json`.

Not modelled: Polymarket US combo fees and maker rebates, polymarket.com maker and taker rebate
programs, polymarket.com per-market `feeSchedule` exceptions, and Kalshi's `flat` fee type
(Specific Trading Fees Table; no series used it on 2026-11-14). Kalshi's per-series multipliers
are in `kalshi_series.json` (see above). Check the market when it matters.

## Sources

| Platform | Source | Rates current as of |
|---|---|---|
| Kalshi | <https://kalshi.com/docs/kalshi-fee-schedule.pdf> (version 7.7.26) | 2026-11-10 |
| Kalshi series | <https://external-api.kalshi.com/trade-api/v2/series> (`fee_type`, `fee_multiplier`) | 2026-11-14 |
| Polymarket US | <https://docs.polymarket.us/fees> | 2026-11-09 |
| polymarket.com | <https://docs.polymarket.com/trading/fees> | 2026-11-08 |

The tests lock the formulas to the worked examples and fee tables on these pages, with a link
to the source in each test.

## Updating the rates

All rates live in `src/pmfees/data/prediction_market_fees.json`. Each platform has its own `as_of`
(the date its rates were last checked against `source_url`). SwapHunt checks the official fee pages
every week; when a rate changes, the updated file arrives here as a pull request, and a new release
follows after review. To use newer rates without waiting for a release:

```python
from pmfees import fee, load_rates

rates = load_rates("prediction_market_fees.json")
fee("kalshi", 0.50, 100, rates=rates)
```

or set `PMFEES_RATES_FILE=/path/to/prediction_market_fees.json`, or pass `--rates` on the
command line. A file with a missing field or an unknown rounding mode is rejected with a
`RatesError` that names the field.

## Development

```bash
pip install -e . pytest ruff mypy
pytest
ruff check . && ruff format --check .
mypy
```

Releases go to PyPI from GitHub Actions with trusted publishing: bump `__version__`, add a
CHANGELOG entry, and push a `v<version>` tag. The publish job waits for approval.

## Limitations

- pmfees checks `contracts` against each platform's general order size step, not the step of a single
  market. Most Polymarket US markets trade in steps of 0.01 contracts, but a few, mostly futures, trade
  whole contracts only ([Fractional Contracts](https://docs.polymarket.us/learn/trading/basics/fractional-shares.md)).
  pmfees cannot see which market an order is for, so it accepts 0.01 steps on those markets too. Check the
  market's step yourself for Polymarket US futures.

## Disclaimer

Fee estimates for information only. The platform's own confirmation is what you pay.

## License

MIT. See [LICENSE](LICENSE).
