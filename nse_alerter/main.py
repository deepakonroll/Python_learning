#!/usr/bin/env python
"""Entry point for the NSE alerter.

    python main.py                # one evaluation (what Task Scheduler runs)
    python main.py --dry-run      # preview anywhere, sends nothing, state untouched
    python main.py --test-notify  # ping Telegram once to verify credentials
    python main.py --verbose      # debug logging
"""

from __future__ import annotations

import argparse
import sys

from nse_alerts.app import run
from nse_alerts.config import ConfigError, load_config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NSE 5m EMA20 cross alerter")
    parser.add_argument("--dry-run", action="store_true",
                        help="evaluate and print, but never notify or write state")
    parser.add_argument("--test-notify", action="store_true",
                        help="send a Telegram test message and exit")
    parser.add_argument("--replay", metavar="YYYY-MM-DD", default=None,
                        help="print every cross that fired on a past session (no sends)")
    parser.add_argument("--symbol", metavar="SYM[>PROXY]", default=None,
                        help="scan just this watch, ignoring SYMBOLS: NIFTY1! | "
                             "MCX:CRUDEOIL | 'MCX:CRUDEOIL>BZ=F' (quote > in shells)")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    parser.add_argument("--once", action="store_true",
                        help="(default behaviour) single evaluation per invocation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    if args.symbol:
        from dataclasses import replace

        from nse_alerts.config import parse_watches
        try:
            watches = parse_watches(args.symbol)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 1
        cfg = replace(cfg, watches=watches, symbol=watches[0].label,
                      yahoo_symbol=watches[0].yahoo_symbol or cfg.yahoo_symbol)
    if args.replay:
        from datetime import date as _date
        from nse_alerts.app import replay
        try:
            target = _date.fromisoformat(args.replay)
        except ValueError as exc:
            print(f"bad --replay date {args.replay!r} (want YYYY-MM-DD)", file=sys.stderr)
            return 1
        replay(cfg, target, verbose=args.verbose)
        return 0
    return run(cfg, dry_run=args.dry_run, test_notify=args.test_notify,
               verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
