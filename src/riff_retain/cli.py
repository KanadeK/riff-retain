"""Command line interface; reports go to stdout and never overwrite input files."""

import argparse
import json
import sys

from . import __version__
from .core import InputError, compare, load_receipt, snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify opaque WAVE chunk retention after editing")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("snapshot", help="write a JSON receipt to stdout")
    scan.add_argument("wave")
    check = commands.add_parser("compare", help="compare a receipt against a current WAVE file")
    check.add_argument("receipt")
    check.add_argument("wave")
    check.add_argument(
        "--allow-chunk",
        action="append",
        default=[],
        metavar="KEY",
        help="permit edits to this exact key, e.g. bext or LIST:INFO; repeatable",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(args.wave)
        else:
            result = compare(
                load_receipt(args.receipt), snapshot(args.wave), tuple(args.allow_chunk)
            )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result.get("ok", True) else 1
    except (InputError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
