"""Tests for postino CLI: version, config validate, config show."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from cli.cli import main

_MINIMAL_VALID = """
    [server]
    secret_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    debug = true
    allowed_hosts = ["localhost"]
    timezone = "UTC"
    base_url = "http://localhost:8000"

    [database]
    url = "sqlite:///test.sqlite3"

    [email]
    provider = "console"
    from_name = "Test"
    from_email = "test@example.com"

    [security]
    unsubscribe_secret = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    [gdpr]

    [branding]

    [sentry]
"""


def _write_config(tmp_path: Path, toml_content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(toml_content))
    return p


class TestVersion:
    def test_version_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        assert "postino" in result.output

    def test_version_format(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version"])
        assert result.exit_code == 0
        parts = result.output.strip().split()
        assert len(parts) == 2
        assert parts[0] == "postino"

    def test_version_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert "version" in parsed["data"]
        assert "timestamp" in parsed["meta"]

    def test_version_json_envelope_keys(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["version", "--json"])
        parsed = json.loads(result.output)
        assert set(parsed.keys()) == {"ok", "data", "meta"}


class TestConfigValidate:
    def test_valid_config(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "validate"])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_invalid_config_exits_1(self, tmp_path: Path, monkeypatch: Any) -> None:
        toml = _MINIMAL_VALID.replace(
            'secret_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            'secret_key = ""',
        )
        path = _write_config(tmp_path, toml)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "validate"])
        assert result.exit_code == 1
        assert "error" in result.output.lower() or "secret_key" in result.output.lower()

    def test_valid_config_json(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "validate", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["valid"] is True

    def test_invalid_config_json_exits_1(self, tmp_path: Path, monkeypatch: Any) -> None:
        toml = _MINIMAL_VALID.replace(
            'secret_key = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            'secret_key = ""',
        )
        path = _write_config(tmp_path, toml)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "validate", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "error" in parsed
        assert "secret_key" in parsed["error"]


class TestConfigShow:
    def test_show_human_readable(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show"])
        assert result.exit_code == 0
        assert "[server]" in result.output
        assert "debug = true" in result.output

    def test_show_json_output(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert "data" in parsed
        assert "meta" in parsed
        assert "timestamp" in parsed["meta"]

    def test_show_json_redacts_secrets(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        server = parsed["data"]["server"]
        assert server["secret_key"] == "***REDACTED***"
        security = parsed["data"]["security"]
        assert security["unsubscribe_secret"] == "***REDACTED***"

    def test_show_single_section(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show", "--section", "branding"])
        assert result.exit_code == 0
        assert "[branding]" in result.output
        assert "[server]" not in result.output

    def test_show_unknown_section_exits_1(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show", "--section", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown section" in result.output

    def test_show_unknown_section_json_exits_1(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show", "--json", "--section", "nonexistent"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "error" in parsed
        assert "Unknown section" in parsed["error"]

    def test_show_json_envelope_structure(self, tmp_path: Path, monkeypatch: Any) -> None:
        path = _write_config(tmp_path, _MINIMAL_VALID)
        monkeypatch.setenv("POSTINO_CONFIG", str(path))
        runner = CliRunner()
        result = runner.invoke(main, ["config", "show", "--json"])
        parsed = json.loads(result.output)
        assert set(parsed.keys()) == {"ok", "data", "meta"}
        assert set(parsed["meta"].keys()) == {"timestamp"}
