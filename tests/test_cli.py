from __future__ import annotations

from pathlib import Path

from jobscout.cli import main


def test_config_check(config_path: Path, capsys) -> None:
    exit_code = main(["config", "check", "--config", str(config_path)])

    assert exit_code == 0
    assert "Configuration valid" in capsys.readouterr().out


def test_missing_config_returns_one(tmp_path: Path, capsys) -> None:
    exit_code = main(["config", "check", "--config", str(tmp_path / "missing.toml")])

    assert exit_code == 1
    assert "Configuration not found" in capsys.readouterr().err
