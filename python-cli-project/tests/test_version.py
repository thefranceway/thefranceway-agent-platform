"""Tests for the version subcommand."""

import pytest
from my_cli import __version__
from my_cli.main import main


def test_version_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == __version__
