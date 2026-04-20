"""Entry point — argument parsing and subcommand dispatch."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from my_cli import __version__
from my_cli.commands.greet import add_greet_parser, run_greet
from my_cli.commands.version import add_version_parser, run_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="my-cli",
        description="A Python CLI application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        metavar="<command>",
    )
    subparsers.required = True

    add_greet_parser(subparsers)
    add_version_parser(subparsers)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns an exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "greet": run_greet,
        "version": run_version,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args) or 0


if __name__ == "__main__":
    sys.exit(main())
