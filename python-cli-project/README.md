# my-cli

A Python CLI application built with `argparse` and a `src/` layout.

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

```bash
my-cli --help
my-cli greet --name Alice
my-cli greet --name Alice --shout
my-cli version
```

## Development

```bash
# Run tests
pytest

# Lint
ruff check src/ tests/

# Type-check
mypy src/
```

## Project Structure

```
python-cli-project/
├── pyproject.toml
├── README.md
├── src/
│   └── my_cli/
│       ├── __init__.py
│       ├── main.py        # Entry point & argument parsing
│       └── commands/
│           ├── __init__.py
│           ├── greet.py
│           └── version.py
└── tests/
    ├── __init__.py
    ├── test_greet.py
    └── test_version.py
```
