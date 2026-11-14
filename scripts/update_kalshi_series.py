#!/usr/bin/env python3
"""Rebuild src/pmfees/data/kalshi_series.json from Kalshi's public Trade API.

Get Series List (GET /series) returns every series with its ``fee_type`` and ``fee_multiplier``.
Most series are standard (``quadratic``, multiplier 1); only the others are written to the file,
so pmfees treats any series that is not listed as standard.

    python3 scripts/update_kalshi_series.py                    # fetch and write
    python3 scripts/update_kalshi_series.py --input series.json  # use a saved API response
    python3 scripts/update_kalshi_series.py --check            # exit 1 if the file is out of date

Only the standard library is used. The fee schedule PDF on kalshi.com is not fetched (it sits
behind a bot challenge); its URL and version are copied from prediction_market_fees.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "pmfees" / "data" / "kalshi_series.json"
RATES = ROOT / "src" / "pmfees" / "data" / "prediction_market_fees.json"
API = "https://external-api.kalshi.com/trade-api/v2/series"
DOC = "https://docs.kalshi.com/api-reference/market/get-series"
USER_AGENT = "pmfees-series-update (+https://github.com/swaphunt/pmfees)"
DEFAULT = {"fee_type": "quadratic", "multiplier": 1}

# Kalshi OpenAPI, Series.fee_type: "'quadratic' is described by the General Trading Fees Table,
# 'quadratic_with_maker_fees' is described by the General Trading Fees Table with maker fees
# described in the Maker Fees section, 'quadratic_with_combo_maker_fees' is the same maker-fee
# structure with a 0.5 maker multiplier instead of 0.25, 'flat' is described by the Specific
# Trading Fees Table." maker_share is that maker multiplier: maker fee = taker formula × share.
FEE_TYPES = {
    "quadratic": {
        "maker_share": 0,
        "description": "General Trading Fees Table; makers pay no fee",
    },
    "quadratic_with_maker_fees": {
        "maker_share": 0.25,
        "description": "General Trading Fees Table with maker fees (Maker Fees): 0.25 × the taker fee",
    },
    "quadratic_with_combo_maker_fees": {
        "maker_share": 0.5,
        "description": "General Trading Fees Table, with combo maker fees: 0.5 × the taker fee",
    },
}


def fetch() -> dict:
    req = urllib.request.Request(API, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())


def build(api: dict, as_of: str) -> dict:
    series = api.get("series")
    if not isinstance(series, list) or not series:
        raise SystemExit("Get Series List: 'series' is missing or empty")
    kalshi = json.loads(RATES.read_text(encoding="utf-8"))["kalshi"]
    out: dict[str, dict] = {}
    for s in series:
        ticker, ft, m = s.get("ticker"), s.get("fee_type"), s.get("fee_multiplier")
        if not ticker or ft is None or not isinstance(m, (int, float)):
            raise SystemExit(f"series without ticker, fee_type or fee_multiplier: {s.get('ticker')!r}")
        if ft not in FEE_TYPES:
            # 'flat' (Specific Trading Fees Table) or a new type: pmfees cannot compute it, so stop
            # here instead of writing a file that silently treats the series as standard.
            raise SystemExit(f"{ticker}: fee_type {ft!r} is not modelled by pmfees")
        mult = int(m) if float(m).is_integer() else m
        if {"fee_type": ft, "multiplier": mult} == DEFAULT:
            continue
        out[ticker] = {"fee_type": ft, "multiplier": mult, "title": (s.get("title") or "").strip()}
    return {
        "as_of": as_of,
        "source_url": API,
        "source_doc_url": DOC,
        "fee_schedule_url": kalshi["source_url"],
        "fee_schedule_version": kalshi["version"],
        "fee_schedule_date": kalshi["source_date"],
        "note": (
            "Series whose fee_type or fee_multiplier differs from the default, from Kalshi's Get Series "
            "List. Series that are not listed are standard. Rates (0.07 taker) come from the fee schedule "
            "in prediction_market_fees.json."
        ),
        "series_total": len(series),
        "default": DEFAULT,
        "fee_types": FEE_TYPES,
        "series": dict(sorted(out.items())),
    }


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, help="saved Get Series List response instead of fetching")
    p.add_argument("--as-of", default=dt.date.today().isoformat(), help="date of the API response")
    p.add_argument("--check", action="store_true", help="compare with the file; write nothing")
    args = p.parse_args()
    api = json.loads(args.input.read_text(encoding="utf-8")) if args.input else fetch()
    new = build(api, args.as_of)
    if args.check:
        old_fees, new_fees = (
            {t: (s["fee_type"], s["multiplier"]) for t, s in d["series"].items()}
            for d in (json.loads(TARGET.read_text(encoding="utf-8")), new)
        )
        if old_fees == new_fees:
            print(f"up to date: {len(new_fees)} non-standard series of {new['series_total']}")
            return 0
        added = sorted(set(new_fees) - set(old_fees))
        removed = sorted(set(old_fees) - set(new_fees))
        changed = sorted(t for t in set(new_fees) & set(old_fees) if new_fees[t] != old_fees[t])
        print(f"out of date: added {added}, removed {removed}, changed {changed}")
        return 1
    TARGET.write_text(render(new), encoding="utf-8")
    print(
        f"wrote {TARGET.relative_to(ROOT)}: {len(new['series'])} non-standard series of {new['series_total']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
