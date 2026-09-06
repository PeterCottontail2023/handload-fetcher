"""Handload Fetcher -- look up load data for a cartridge across sources.

    python3 -m handloadfetcher                                  # interactive: just asks for a cartridge
    python3 -m handloadfetcher "9mm luger"                       # one-shot
    python3 -m handloadfetcher "308 winchester" --sources barnes,sierra
    python3 -m handloadfetcher "9mm luger" --no-report           # skip the HTML report
"""
from __future__ import annotations

import argparse
import sys

from . import __author__, __author_url__, __title__, report
from .http import new_session
from .sources import barnes, hammer, nosler, sierra, speer

SOURCES = {
    "nosler": nosler,
    "sierra": sierra,
    "speer": speer,
    "hammer": hammer,
    "barnes": barnes,
    # "hodgdon": hodgdon,  # not yet implemented -- Cloudflare-protected
}

BANNER = f"{__title__} — by {__author__} ({__author_url__})"


def get_load_data(cartridge_query: str, source_names: list[str] | None = None):
    """Fetch load data for `cartridge_query` from each named source (default: all).

    Returns a list of models.SourceResult, one per source, in source order.
    """
    names = source_names or list(SOURCES)
    session = new_session()
    results = []
    for name in names:
        mod = SOURCES.get(name)
        if mod is None:
            raise ValueError(f"unknown source: {name!r} (available: {', '.join(SOURCES)})")
        results.append(mod.fetch(cartridge_query, session))
    return results


def run_one(cartridge: str, names: list[str], no_report: bool, no_open: bool, report_dir: str | None) -> bool:
    """Run one search: print it to the terminal, write/open the HTML report.

    Returns True if any source returned data.
    """
    results = get_load_data(cartridge, names)

    any_data = False
    for r in results:
        print(f"\n=== {r.manufacturer} ===")
        if r.matched_cartridge:
            print(f"matched cartridge: {r.matched_cartridge}")
        if r.errors:
            for e in r.errors:
                print(f"  ! {e}")
        for t in r.tables:
            any_data = True
            print()
            print(t)
    print()

    if not no_report:
        path = report.write_report(cartridge, results, report_dir)
        print(f"report: {path}")
        if not no_open:
            report.open_in_browser(path)

    if not any_data:
        print(f"No load data found for {cartridge!r} from any of: {', '.join(names)}.")
    return any_data


def _interactive_loop(names: list[str], no_report: bool, no_open: bool, report_dir: str | None) -> int:
    print(BANNER)
    print("Looks up reloading load data for a cartridge across manufacturer sites.")
    print("Type a cartridge name (e.g. \"9mm luger\" or \".308 win\"), or just press Enter to quit.\n")
    while True:
        try:
            query = input("Cartridge: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return 0
        if not query or query.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            return 0
        try:
            run_one(query, names, no_report, no_open, report_dir)
        except Exception as exc:  # noqa: BLE001 -- keep the prompt alive on a bad search
            print(f"Something went wrong looking that up: {exc}")
        print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cartridge",
        nargs="?",
        default=None,
        help='cartridge name, e.g. "9mm luger" or ".308 win" -- omit this to run interactively instead',
    )
    parser.add_argument(
        "--sources",
        default=",".join(SOURCES),
        help=f"comma-separated source list (default: all -- {', '.join(SOURCES)})",
    )
    parser.add_argument(
        "--no-report", action="store_true", help="skip generating/opening the HTML report"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="still write the HTML report, but don't open it"
    )
    parser.add_argument(
        "--report-dir", default=None, help="directory for the HTML report (default: handloadfetcher/reports/)"
    )
    args = parser.parse_args(argv)
    names = [s.strip() for s in args.sources.split(",") if s.strip()]

    if args.cartridge is None:
        return _interactive_loop(names, args.no_report, args.no_open, args.report_dir)

    print(BANNER)
    any_data = run_one(args.cartridge, names, args.no_report, args.no_open, args.report_dir)
    return 0 if any_data else 1


if __name__ == "__main__":
    sys.exit(main())
