"""version subcommand — print package version."""

import argparse

from my_cli import __version__


def add_version_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    subparsers.add_parser(
        "version",
        help="Print the current version and exit",
    )


def run_version(args: argparse.Namespace) -> int:
    print(__version__)
    return 0
