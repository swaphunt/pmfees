"""Command line: ``pmfees kalshi --price 0.50 --contracts 100``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal

from . import __version__
from .core import BreakEven, FeeResult, break_even, fee
from .rates import Rates, RatesError, load_rates


def _money(d: Decimal) -> str:
    # Show at least cents, more when the platform rounds finer (polymarket.com: 5 decimals).
    exp = d.normalize().as_tuple().exponent
    places = max(2, -exp if isinstance(exp, int) else 2)
    return f"${d:,.{places}f}"


def _price(d: Decimal) -> str:
    return f"{(d * 100).normalize():f}¢"


def _render_fee(r: FeeResult) -> str:
    pct = r.fee_pct_of_trade_value
    pct_s = "" if pct is None else f" ({pct.quantize(Decimal('0.01'))}% of trade value at {_price(r.price)})"
    lines = [
        f"{r.platform_name} {r.role} fee: {_money(r.fee)}{pct_s}",
        f"  {r.side} {r.contracts.normalize():f} contracts at {_money(r.price)}"
        f" = {_money(r.trade_value)} trade value",
    ]
    for k, v in r.details.items():
        lines.append(f"  {k.replace('_', ' ')}: {v}")
    lines.append(
        f"  {r.coefficient.normalize():f} × {r.contracts.normalize():f} × {r.price.normalize():f}"
        f" × (1 − {r.price.normalize():f}) = {r.raw_fee.normalize():f}, {r.rounding}: {_money(r.fee)}"
    )
    lines += [f"  note: {n}" for n in r.notes]
    lines.append(f"  rates as of {r.rates_as_of} · source: {r.source_url}")
    return "\n".join(lines)


def _render_break_even(b: BreakEven) -> str:
    verb = "rise" if b.side == "buy" else "fall"
    if b.exit_price is None or b.move is None or b.exit_fee is None or b.profit_at_exit is None:
        return (
            f"Break-even: no exit price below $1 puts this {b.side} in profit after fees ({b.platform_name})."
        )
    return (
        f"Break-even: the price must {verb} {abs(b.move * 100).normalize():f}¢ "
        f"to {_price(b.exit_price)} (tick {_price(b.tick)}); "
        f"entry fee {_money(b.entry_fee)} + exit fee {_money(b.exit_fee)}, "
        f"profit at that price {_money(b.profit_at_exit)}"
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pmfees",
        description="Exact trading fees for Kalshi, Polymarket US and polymarket.com.",
    )
    p.add_argument("--version", action="version", version=f"pmfees {__version__}")
    sub = p.add_subparsers(dest="platform", required=True, metavar="PLATFORM")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--price", required=True, help="contract price in dollars, e.g. 0.50")
    common.add_argument("--contracts", required=True, help="number of contracts (shares)")
    common.add_argument("--side", choices=("buy", "sell"), default="buy")
    common.add_argument("--maker", action="store_true", help="maker order instead of taker")
    common.add_argument("--break-even", action="store_true", help="also show the break-even price move")
    common.add_argument("--tick", default="0.01", help="price step for --break-even (default 0.01)")
    common.add_argument("--json", action="store_true", help="print JSON instead of text")
    common.add_argument("--rates", help="path to an updated prediction_market_fees.json")

    k = sub.add_parser("kalshi", parents=[common], help="Kalshi")
    km = k.add_mutually_exclusive_group()
    km.add_argument("--multiplier", help="fee multiplier M (default 1 for takers, 0 for makers)")
    km.add_argument(
        "--series", help="series, event or market ticker, e.g. KXMLBGAME: M and maker fees from Kalshi"
    )

    us = sub.add_parser("polymarket-us", parents=[common], help="Polymarket US")
    grp = us.add_mutually_exclusive_group()
    grp.add_argument("--tier", help="taker tier 1-4, or 'new' for the first 7 days (default 1)")
    grp.add_argument("--volume", help="trailing 30-day taker volume in USD (picks the tier)")

    g = sub.add_parser("polymarket", parents=[common], help="polymarket.com (international)")
    g.add_argument("--category", required=True, help="market category, e.g. Crypto, Sports, Politics")

    r = sub.add_parser("rates", help="show the loaded rates and their date")
    r.add_argument("--rates", help="path to an updated prediction_market_fees.json")
    return p


def _rates_summary(rates: Rates) -> str:
    g, us, k = rates.polymarket_global, rates.polymarket_us, rates.kalshi
    lines = [f"Rates current as of {rates.as_of} ({rates.path})", "", f"Kalshi ({k.source_url})"]
    lines.append(
        f"  taker {k.taker_rate}, maker {k.maker_rate}, series multipliers "
        f"{', '.join(str(m) for m in k.multipliers)}; {k.rounding.describe()}"
    )
    lines += ["", f"Polymarket US ({us.source_url}, effective {us.effective})"]
    for t in us.tiers:
        rng = f"below ${t.below:,}" if t.below is not None else "the rest"
        lines.append(f"  {t.label}: Θ {t.theta} (30-day taker volume {rng})")
    lines.append(f"  New account: Θ {us.new_account_theta}; {us.rounding.describe()}")
    lines += ["", f"polymarket.com ({g.source_url})"]
    lines += [f"  {cat}: {rate}" for cat, rate in g.rates.items()]
    lines.append(f"  {g.rounding.describe()}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rates = load_rates(args.rates)
        if args.platform == "rates":
            print(_rates_summary(rates))
            return 0
        opts: dict[str, str | None] = {
            "multiplier": getattr(args, "multiplier", None),
            "series": getattr(args, "series", None),
            "tier": getattr(args, "tier", None),
            "volume_30d": getattr(args, "volume", None),
            "category": getattr(args, "category", None),
        }
        result = fee(
            args.platform,
            args.price,
            args.contracts,
            args.side,
            opts["tier"],
            args.maker,
            category=opts["category"],
            multiplier=opts["multiplier"],
            series=opts["series"],
            volume_30d=opts["volume_30d"],
            rates=rates,
        )
        be = None
        if args.break_even:
            be = break_even(
                args.platform,
                args.price,
                args.contracts,
                args.side,
                opts["tier"],
                args.maker,
                tick=args.tick,
                category=opts["category"],
                multiplier=opts["multiplier"],
                series=opts["series"],
                volume_30d=opts["volume_30d"],
                rates=rates,
            )
    except (ValueError, ArithmeticError, RatesError, OSError) as exc:
        print(f"pmfees: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        out: dict[str, object] = {"fee": result.breakdown(), "notes": list(result.notes)}
        if be is not None:
            out["break_even"] = {
                "entry_price": str(be.entry_price),
                "exit_price": None if be.exit_price is None else str(be.exit_price),
                "move": None if be.move is None else str(be.move),
                "entry_fee_usd": str(be.entry_fee),
                "exit_fee_usd": None if be.exit_fee is None else str(be.exit_fee),
                "profit_at_exit_usd": None if be.profit_at_exit is None else str(be.profit_at_exit),
                "tick": str(be.tick),
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(_render_fee(result))
        if be is not None:
            print(_render_break_even(be))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
