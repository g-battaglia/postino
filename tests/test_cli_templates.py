"""Tests for postino CLI: templates list, get, create."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from apps.templates_mgr.models import EmailTemplate
from cli.cli import main


@pytest.mark.django_db
class TestTemplatesList:
    def test_templates_list_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "list"])
        assert result.exit_code == 0
        assert "No templates found" in result.output

    def test_templates_list_with_data(self) -> None:
        EmailTemplate.objects.create(
            name="Welcome", slug="welcome",
            subject_default="Hi", html_body="<p>Hi</p>",
        )
        EmailTemplate.objects.create(
            name="Bye", slug="bye",
            subject_default="Bye", html_body="<p>Bye</p>",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "list"])
        assert result.exit_code == 0
        assert "welcome" in result.output
        assert "bye" in result.output
        assert "Showing 2 template(s)" in result.output

    def test_templates_list_json(self) -> None:
        EmailTemplate.objects.create(
            name="Welcome", slug="welcome",
            subject_default="Hi", html_body="<p>Hi</p>",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert "timestamp" in parsed["meta"]
        assert parsed["data"]["count"] == 1
        assert parsed["data"]["templates"][0]["slug"] == "welcome"

    def test_templates_list_json_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "list", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["count"] == 0


@pytest.mark.django_db
class TestTemplatesGet:
    def test_templates_get_by_slug(self) -> None:
        EmailTemplate.objects.create(
            name="Welcome", slug="welcome",
            subject_default="Hi {{ name }}", html_body="<p>Hi</p>",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "get", "welcome"])
        assert result.exit_code == 0
        assert "welcome" in result.output
        assert "Hi {{ name }}" in result.output

    def test_templates_get_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "get", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_templates_get_not_found_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "get", "nonexistent", "--json"])
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "not found" in parsed["error"].lower()

    def test_templates_get_json(self) -> None:
        EmailTemplate.objects.create(
            name="Welcome", slug="welcome",
            subject_default="Hi", html_body="<p>Hello</p>", text_body="Hello",
        )
        runner = CliRunner()
        result = runner.invoke(main, ["templates", "get", "welcome", "--json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["slug"] == "welcome"
        assert parsed["data"]["name"] == "Welcome"
        assert parsed["data"]["html_body"] == "<p>Hello</p>"
        assert parsed["data"]["text_body"] == "Hello"


@pytest.mark.django_db
class TestTemplatesCreate:
    def test_templates_create_basic(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "templates", "create",
                "--name", "Newsletter",
                "--slug", "newsletter",
                "--subject", "Weekly News",
                "--html-body", "<p>Content</p>",
            ],
        )
        assert result.exit_code == 0
        assert "Created template 'Newsletter'" in result.output
        assert EmailTemplate.objects.filter(slug="newsletter").exists()

    def test_templates_create_with_text_body(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "templates", "create",
                "--name", "With Text",
                "--slug", "with-text",
                "--subject", "Hello",
                "--html-body", "<p>Hi</p>",
                "--text-body", "Hi there",
            ],
        )
        assert result.exit_code == 0
        tmpl = EmailTemplate.objects.get(slug="with-text")
        assert tmpl.text_body == "Hi there"

    def test_templates_create_json(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "templates", "create",
                "--name", "JSON Template",
                "--slug", "json-tmpl",
                "--subject", "Hello {{ name }}",
                "--html-body", "<p>Hi</p>",
                "--json",
            ],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["ok"] is True
        assert parsed["data"]["slug"] == "json-tmpl"
        assert parsed["data"]["subject_default"] == "Hello {{ name }}"

    def test_templates_create_duplicate_slug(self) -> None:
        EmailTemplate.objects.create(
            name="Existing", slug="existing",
            subject_default="X", html_body="<p>X</p>",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "templates", "create",
                "--name", "Duplicate",
                "--slug", "existing",
                "--subject", "Y",
                "--html-body", "<p>Y</p>",
            ],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_templates_create_duplicate_slug_json(self) -> None:
        EmailTemplate.objects.create(
            name="Existing", slug="dup",
            subject_default="X", html_body="<p>X</p>",
        )
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "templates", "create",
                "--name", "Dup2",
                "--slug", "dup",
                "--subject", "Y",
                "--html-body", "<p>Y</p>",
                "--json",
            ],
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["ok"] is False
        assert "already exists" in parsed["error"]
