"""Tests for postino CLI: subscribers list, get, add, count."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from apps.subscribers.models import Subscriber, Tag
from apps.subscribers.services import add_subscriber, suppress_subscriber
from cli.cli import main


@pytest.fixture()
def _setup_django_for_cli() -> None:
    """Ensure Django is set up before CLI commands access the ORM."""
    from cli.cli import _setup_django

    _setup_django()


@pytest.mark.django_db
class TestSubscribersList:
    def test_subscribers_list_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "list"])
        assert result.exit_code == 0
        assert "No subscribers found" in result.output

    def test_subscribers_list_with_data(self) -> None:
        add_subscriber("alice@example.com", name="Alice")
        add_subscriber("bob@example.com", name="Bob")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "list"])
        assert result.exit_code == 0
        assert "alice@example.com" in result.output
        assert "bob@example.com" in result.output
        assert "Showing 2 of 2" in result.output

    def test_subscribers_list_json_output(self) -> None:
        add_subscriber("alice@example.com", name="Alice")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert "timestamp" in parsed["meta"]
        data = parsed["data"]
        assert data["total"] == 1
        assert data["subscribers"][0]["email"] == "alice@example.com"

    def test_subscribers_list_status_filter(self) -> None:
        add_subscriber("pending@example.com")
        sub = add_subscriber("to-suppress@example.com")
        suppress_subscriber(sub, reason="test")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "list", "--status", "pending"])
        assert result.exit_code == 0
        assert "pending@example.com" in result.output
        assert "to-suppress@example.com" not in result.output


@pytest.mark.django_db
class TestSubscribersGet:
    def test_subscribers_get_by_email(self) -> None:
        add_subscriber("alice@example.com", name="Alice")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "get", "alice@example.com"])
        assert result.exit_code == 0
        assert "alice@example.com" in result.output
        assert "Alice" in result.output

    def test_subscribers_get_by_uuid(self) -> None:
        subscriber = add_subscriber("uuid@example.com", name="UUID User")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "get", str(subscriber.id)])
        assert result.exit_code == 0
        assert "uuid@example.com" in result.output

    def test_subscribers_get_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "get", "nobody@example.com"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_subscribers_get_not_found_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "get", "nobody@example.com", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()

    def test_subscribers_get_json_output(self) -> None:
        add_subscriber("json@example.com", name="JSON User")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "get", "json@example.com", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["email"] == "json@example.com"
        assert parsed["data"]["name"] == "JSON User"


@pytest.mark.django_db
class TestSubscribersAdd:
    def test_subscribers_add_basic(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "add", "new@example.com"])
        assert result.exit_code == 0
        assert "Added subscriber new@example.com" in result.output
        assert Subscriber.objects.filter(email="new@example.com").exists()

    def test_subscribers_add_with_tags_and_metadata(self) -> None:
        Tag.objects.create(name="vip", display_name="VIP")
        Tag.objects.create(name="beta", display_name="Beta")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "subscribers",
                "add",
                "tagged@example.com",
                "--name",
                "Tagged",
                "--tag",
                "vip",
                "--tag",
                "beta",
                "--metadata",
                "source=web",
                "--metadata",
                "ref=abc",
            ],
        )
        assert result.exit_code == 0

        sub = Subscriber.objects.get(email="tagged@example.com")
        assert sub.name == "Tagged"
        tag_names = list(sub.tags.values_list("name", flat=True))
        assert "vip" in tag_names
        assert "beta" in tag_names
        assert sub.metadata == {"source": "web", "ref": "abc"}

    def test_subscribers_add_suppressed_raises(self) -> None:
        sub = add_subscriber("suppressed@example.com")
        suppress_subscriber(sub, reason="test")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "add", "suppressed@example.com"])
        assert result.exit_code == 1
        assert "suppressed" in result.output.lower()

    def test_subscribers_add_json_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["subscribers", "add", "jsonnew@example.com", "--json"]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["email"] == "jsonnew@example.com"

    def test_subscribers_add_invalid_metadata_format(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["subscribers", "add", "bad@example.com", "--metadata", "noequals"]
        )
        assert result.exit_code == 1
        assert "Invalid metadata format" in result.output

    def test_subscribers_add_invalid_metadata_format_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["subscribers", "add", "bad@example.com", "--metadata", "noequals", "--json"]
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "Invalid metadata format" in parsed["error"]

    def test_subscribers_add_suppressed_json(self) -> None:
        sub = add_subscriber("suppressed2@example.com")
        suppress_subscriber(sub, reason="test")

        runner = CliRunner()
        result = runner.invoke(
            main, ["subscribers", "add", "suppressed2@example.com", "--json"]
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "suppressed" in parsed["error"].lower()


@pytest.mark.django_db
class TestSubscribersCount:
    def test_subscribers_count(self) -> None:
        add_subscriber("a@example.com")
        add_subscriber("b@example.com")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "count"])
        assert result.exit_code == 0
        assert "2 subscribers" in result.output

    def test_subscribers_count_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "count"])
        assert result.exit_code == 0
        assert "0 subscribers" in result.output

    def test_subscribers_count_json(self) -> None:
        add_subscriber("c@example.com")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "count", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["count"] == 1

    def test_subscribers_count_with_status_filter(self) -> None:
        add_subscriber("pending2@example.com")
        sub = add_subscriber("gone2@example.com")
        suppress_subscriber(sub, reason="test")

        runner = CliRunner()
        result = runner.invoke(main, ["subscribers", "count", "--status", "pending"])
        assert result.exit_code == 0
        assert "1 subscriber" in result.output
