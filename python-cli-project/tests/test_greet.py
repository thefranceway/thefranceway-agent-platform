"""Tests for the greet subcommand."""

import pytest
from my_cli.main import main


def test_greet_basic(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["greet", "--name", "Alice"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "Hello, Alice!"


def test_greet_shout(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["greet", "--name", "Alice", "--shout"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "HELLO, ALICE!"


def test_greet_short_flag(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["greet", "-n", "Bob"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Bob" in captured.out


def test_greet_missing_name() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["greet"])
    assert exc_info.value.code != 0
