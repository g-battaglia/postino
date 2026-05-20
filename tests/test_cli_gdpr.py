"""Tests for postino CLI: gdpr audit, export, delete."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from apps.subscribers.models import Subscriber
from apps.subscribers.services import add_subscriber
from cli.cli import main


@pytest.fixture()
def _ensure_subscriber(settings) -> Subscriber:
    """Create a test subscriber and return it."""
    settings.POSTINO_REQUIRE_DOUBLE_OPTIN = False
    return add_subscriber("gdpr@example.com", name="GDPR Test")


@pytest.mark.django_db
class TestGdprAudit:
    def test_gdpr_audit_shows_consent_records(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "audit", "gdpr@example.com"])
        assert result.exit_code == 0
        assert "gdpr@example.com" in result.output
        assert "Consent Records" in result.output
        assert "grant" in result.output.lower()

    def test_gdpr_audit_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "audit", "missing@example.com"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_gdpr_audit_not_found_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "audit", "missing@example.com", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()

    def test_gdpr_audit_json_output(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "audit", "gdpr@example.com", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        data = parsed["data"]
        assert data["subscriber"]["email"] == "gdpr@example.com"
        assert len(data["consent_records"]) >= 1
        assert "tags" in data


@pytest.mark.django_db
class TestGdprExport:
    def test_gdpr_export_to_file(self, _ensure_subscriber: Subscriber, tmp_path: Path) -> None:
        output_file = tmp_path / "export.json"
        runner = CliRunner()
        result = runner.invoke(
            main, ["gdpr", "export", "gdpr@example.com", "-o", str(output_file)]
        )
        assert result.exit_code == 0
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["subscriber"]["email"] == "gdpr@example.com"
        assert "consent_records" in data
        assert "unsubscribe_events" in data

    def test_gdpr_export_to_stdout(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "export", "gdpr@example.com"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["subscriber"]["email"] == "gdpr@example.com"

    def test_gdpr_export_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "export", "nobody@example.com"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_gdpr_export_json_success(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "export", "gdpr@example.com", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["subscriber"]["email"] == "gdpr@example.com"

    def test_gdpr_export_json_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "export", "nobody@example.com", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()


@pytest.mark.django_db
class TestGdprDelete:
    def test_gdpr_delete_requires_confirm(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "delete", "gdpr@example.com"])
        assert result.exit_code == 1
        assert "WARNING" in result.output
        assert "--confirm" in result.output

        sub = Subscriber.objects.get(email="gdpr@example.com")
        assert sub.status != "deleted"

    def test_gdpr_delete_with_confirm(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "delete", "gdpr@example.com", "--confirm"])
        assert result.exit_code == 0
        assert "Deleted subscriber gdpr@example.com" in result.output

        sub = Subscriber.objects.get(email="gdpr@example.com")
        assert sub.status == "deleted"
        assert sub.name == ""

    def test_gdpr_delete_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "delete", "nobody@example.com", "--confirm"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_gdpr_delete_json_requires_confirm(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["gdpr", "delete", "gdpr@example.com", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "--confirm" in parsed["error"]

    def test_gdpr_delete_json_success(self, _ensure_subscriber: Subscriber) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["gdpr", "delete", "gdpr@example.com", "--confirm", "--json"]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["email"] == "gdpr@example.com"
        assert parsed["data"]["status"] == "deleted"

    def test_gdpr_delete_json_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["gdpr", "delete", "nobody@example.com", "--confirm", "--json"]
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()
