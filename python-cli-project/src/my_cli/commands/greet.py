"""greet subcommand — say hello."""

import argparse


def add_greet_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "greet",
        help="Print a greeting",
        description="Print a personalised greeting to stdout.",
    )
    parser.add_argument(
        "--name", "-n",
        required=True,
        help="Name of the person to greet",
    )
    parser.add_argument(
        "--shout",
        action="store_true",
        default=False,
        help="SHOUT the greeting",
    )


def run_greet(args: argparse.Namespace) -> int:
    message = f"Hello, {args.name}!"
    if args.shout:
        message = message.upper()
    print(message)
    return 0
