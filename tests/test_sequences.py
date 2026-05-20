"""Tests for Phase 4: Sequence models, services, management command, CLI, and views.

Covers sequence evaluation, enrollment lifecycle, triggers, auto-cancel on
unsubscribe/suppression, management command execution, CLI commands, and
admin-only sequence views.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.admin import AdminSite
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client

from apps.campaigns.admin import SequenceAdmin
from apps.campaigns.models import (
    EmailSend,
    Sequence,
    SequenceEnrollment,
    SequenceStep,
)
from apps.campaigns.services import (
    EnrollmentError,
    cancel_enrollments_for_subscriber,
    enroll_subscriber,
    evaluate_sequences,
    pause_sequence,
    resume_sequence,
    trigger_sequences_for_subscriber_created,
    trigger_sequences_for_tag_added,
)
from apps.consent.models import ConsentRecord, EmailType
from apps.consent.services import process_global_unsubscribe
from apps.subscribers.models import Subscriber, Tag
from apps.subscribers.services import add_subscriber, tag_subscriber
from apps.templates_mgr.models import EmailTemplate
from apps.webhooks.services import process_resend_event

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def email_type(db: None) -> EmailType:
    return EmailType.objects.create(slug="onboarding", name="Onboarding")


@pytest.fixture
def email_type_marketing(db: None) -> EmailType:
    return EmailType.objects.create(
        slug="marketing", name="Marketing", is_transactional=False,
    )


@pytest.fixture
def template(db: None) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Welcome",
        slug="welcome",
        subject_default="Welcome {{ subscriber_name }}",
        html_body="<p>Hello {{ subscriber_name }}</p>",
        text_body="Hello {{ subscriber_name }}",
    )


@pytest.fixture
def template2(db: None) -> EmailTemplate:
    return EmailTemplate.objects.create(
        name="Day 2",
        slug="day-2",
        subject_default="Day 2",
        html_body="<p>Day 2</p>",
    )


@pytest.fixture
def subscriber(db: None) -> Subscriber:
    sub = Subscriber.objects.create(
        email="ada@example.com",
        name="Ada Lovelace",
        status=Subscriber.Status.ACTIVE,
    )
    ConsentRecord.objects.create(
        subscriber=sub,
        email_type=None,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )
    return sub


@pytest.fixture
def subscriber_with_consent(db: None, email_type: EmailType) -> Subscriber:
    sub = Subscriber.objects.create(
        email="ada@example.com",
        name="Ada Lovelace",
        status=Subscriber.Status.ACTIVE,
    )
    ConsentRecord.objects.create(
        subscriber=sub,
        email_type=None,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )
    ConsentRecord.objects.create(
        subscriber=sub,
        email_type=email_type,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )
    return sub


@pytest.fixture
def subscriber2(db: None) -> Subscriber:
    sub = Subscriber.objects.create(
        email="grace@example.com",
        name="Grace Hopper",
        status=Subscriber.Status.ACTIVE,
    )
    ConsentRecord.objects.create(
        subscriber=sub,
        email_type=None,
        action=ConsentRecord.Action.GRANT,
        method="test",
    )
    return sub


@pytest.fixture
def sequence(
    email_type: EmailType, template: EmailTemplate,
) -> Sequence:
    seq = Sequence.objects.create(
        name="Onboarding",
        slug="onboarding",
        trigger_type=Sequence.TriggerType.SUBSCRIBER_CREATED,
    )
    SequenceStep.objects.create(
        sequence=seq,
        order=1,
        delay_hours=0,
        email_type=email_type,
        template=template,
    )
    return seq


@pytest.fixture
def multi_step_sequence(
    email_type: EmailType, template: EmailTemplate, template2: EmailTemplate,
) -> Sequence:
    seq = Sequence.objects.create(
        name="Multi-Step",
        slug="multi-step",
        trigger_type=Sequence.TriggerType.TAG_ADDED,
        trigger_config={"tags": ["pro"]},
    )
    SequenceStep.objects.create(
        sequence=seq, order=1, delay_hours=0,
        email_type=email_type, template=template,
    )
    SequenceStep.objects.create(
        sequence=seq, order=2, delay_hours=24,
        email_type=email_type, template=template2,
    )
    return seq


@pytest.fixture
def admin_user(db: None) -> User:
    return User.objects.create_superuser(
        username="admin", email="admin@test.com", password="pass",
    )


@pytest.fixture
def client_logged(client: Client, admin_user: User) -> Client:
    client.login(username="admin", password="pass")
    return client


# ---------------------------------------------------------------------------
# Sequence model
# ---------------------------------------------------------------------------


class TestSequenceModel:
    def test_create_and_str(self, email_type: EmailType, template: EmailTemplate) -> None:
        seq = Sequence.objects.create(
            name="Welcome Flow",
            slug="welcome-flow",
            trigger_type=Sequence.TriggerType.MANUAL,
        )
        assert str(seq) == "Welcome Flow"

    def test_default_is_active(self, email_type: EmailType, template: EmailTemplate) -> None:
        seq = Sequence.objects.create(
            name="Test", slug="test", trigger_type=Sequence.TriggerType.MANUAL,
        )
        assert seq.is_active is True

    def test_trigger_types(self) -> None:
        values = {t.value for t in Sequence.TriggerType}
        assert "subscriber_created" in values
        assert "tag_added" in values
        assert "manual" in values

    def test_slug_unique(self, email_type: EmailType, template: EmailTemplate) -> None:
        from django.db import IntegrityError

        Sequence.objects.create(name="A", slug="dup", trigger_type=Sequence.TriggerType.MANUAL)
        with pytest.raises(IntegrityError):
            Sequence.objects.create(name="B", slug="dup", trigger_type=Sequence.TriggerType.MANUAL)


class TestSequenceStepModel:
    def test_create_and_str(self, sequence: Sequence) -> None:
        step = sequence.steps.first()
        assert "Step 1" in str(step)

    def test_order_unique_per_sequence(
        self, sequence: Sequence, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            SequenceStep.objects.create(
                sequence=sequence, order=1, delay_hours=0,
                email_type=email_type, template=template,
            )

    def test_ordering(self, multi_step_sequence: Sequence) -> None:
        orders = list(multi_step_sequence.steps.values_list("order", flat=True))
        assert orders == [1, 2]


class TestSequenceEnrollmentModel:
    def test_create_and_str(self, subscriber: Subscriber, sequence: Sequence) -> None:
        step = sequence.steps.first()
        enrollment = SequenceEnrollment.objects.create(
            subscriber=subscriber,
            sequence=sequence,
            current_step=step,
        )
        assert "ada@example.com" in str(enrollment)
        assert "Onboarding" in str(enrollment)
        assert "Active" in str(enrollment)

    def test_unique_together(self, subscriber: Subscriber, sequence: Sequence) -> None:
        from django.db import IntegrityError

        SequenceEnrollment.objects.create(subscriber=subscriber, sequence=sequence)
        with pytest.raises(IntegrityError):
            SequenceEnrollment.objects.create(subscriber=subscriber, sequence=sequence)

    def test_default_status_active(self, subscriber: Subscriber, sequence: Sequence) -> None:
        enrollment = SequenceEnrollment.objects.create(
            subscriber=subscriber, sequence=sequence,
        )
        assert enrollment.status == SequenceEnrollment.Status.ACTIVE


# ---------------------------------------------------------------------------
# EmailSend sequence_step FK
# ---------------------------------------------------------------------------


class TestEmailSendSequenceStep:
    def test_sequence_step_fk(
        self, subscriber: Subscriber, sequence: Sequence, email_type: EmailType,
    ) -> None:
        step = sequence.steps.first()
        es = EmailSend.objects.create(
            subscriber=subscriber,
            sequence_step=step,
            email_type=email_type,
            subject_line_used="Welcome",
        )
        assert es.sequence_step == step
        assert es.campaign is None

    def test_sequence_step_nullable(
        self, subscriber: Subscriber, email_type: EmailType,
    ) -> None:
        es = EmailSend.objects.create(
            subscriber=subscriber,
            email_type=email_type,
            subject_line_used="No step",
        )
        assert es.sequence_step is None


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class TestSequenceAdmin:
    def test_sequence_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(Sequence)

    def test_sequence_step_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(SequenceStep)

    def test_enrollment_is_registered(self) -> None:
        from django.contrib import admin

        assert admin.site.is_registered(SequenceEnrollment)

    def test_sequence_admin_has_inline(self) -> None:
        ma = SequenceAdmin(Sequence, AdminSite())
        assert len(ma.inlines) > 0


# ---------------------------------------------------------------------------
# Enrollment services
# ---------------------------------------------------------------------------


class TestEnrollSubscriber:
    def test_enroll_active_subscriber(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enrollment = enroll_subscriber(subscriber, sequence)
        assert enrollment.status == SequenceEnrollment.Status.ACTIVE
        assert enrollment.current_step == sequence.steps.first()

    def test_enroll_suppressed_subscriber_raises(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        subscriber.status = Subscriber.Status.UNSUBSCRIBED
        subscriber.save()
        with pytest.raises(EnrollmentError, match="suppressed"):
            enroll_subscriber(subscriber, sequence)

    def test_enroll_pending_subscriber_raises(
        self, sequence: Sequence,
    ) -> None:
        pending = Subscriber.objects.create(
            email="pending@example.com",
            status=Subscriber.Status.PENDING,
        )
        with pytest.raises(EnrollmentError, match="non-active"):
            enroll_subscriber(pending, sequence)

    def test_enroll_inactive_sequence_raises(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        sequence.is_active = False
        sequence.save()
        with pytest.raises(EnrollmentError, match="inactive"):
            enroll_subscriber(subscriber, sequence)

    def test_duplicate_enrollment_returns_existing(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        first = enroll_subscriber(subscriber, sequence)
        second = enroll_subscriber(subscriber, sequence)
        assert first.pk == second.pk

    def test_cancelled_enrollment_raises(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enrollment = enroll_subscriber(subscriber, sequence)
        enrollment.status = SequenceEnrollment.Status.CANCELLED
        enrollment.save()
        with pytest.raises(EnrollmentError, match="previously"):
            enroll_subscriber(subscriber, sequence)


# ---------------------------------------------------------------------------
# Sequence evaluation
# ---------------------------------------------------------------------------


class TestEvaluateSequences:
    def test_sends_due_email(
        self, subscriber_with_consent: Subscriber, sequence: Sequence,
    ) -> None:
        enrollment = enroll_subscriber(subscriber_with_consent, sequence)
        assert enrollment.current_step.delay_hours == 0

        result = evaluate_sequences()
        assert result.emails_sent == 1
        assert result.enrollments_processed == 1

        enrollment.refresh_from_db()
        assert enrollment.status == SequenceEnrollment.Status.COMPLETED

    def test_skips_early_due_step(
        self, subscriber_with_consent: Subscriber, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        seq = Sequence.objects.create(
            name="Delayed", slug="delayed",
            trigger_type=Sequence.TriggerType.MANUAL,
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=48,
            email_type=email_type, template=template,
        )
        enroll_subscriber(subscriber_with_consent, seq)

        result = evaluate_sequences()
        assert result.emails_sent == 0
        assert result.enrollments_processed == 1

    def test_auto_cancels_suppressed(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        subscriber.status = Subscriber.Status.UNSUBSCRIBED
        subscriber.save()

        result = evaluate_sequences()
        assert result.enrollments_cancelled == 1
        assert result.emails_sent == 0

    def test_step_condition_health_below(
        self, subscriber_with_consent: Subscriber, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        seq = Sequence.objects.create(
            name="Re-engage", slug="re-engage",
            trigger_type=Sequence.TriggerType.MANUAL,
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=0,
            email_type=email_type, template=template,
            condition={"health_below": 30},
        )
        subscriber_with_consent.health_score = 50
        subscriber_with_consent.save()
        enroll_subscriber(subscriber_with_consent, seq)

        result = evaluate_sequences()
        assert result.emails_skipped == 1

    def test_step_condition_has_tag(
        self, subscriber_with_consent: Subscriber, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        seq = Sequence.objects.create(
            name="Pro", slug="pro-flow",
            trigger_type=Sequence.TriggerType.MANUAL,
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=0,
            email_type=email_type, template=template,
            condition={"has_tag": "vip"},
        )
        enroll_subscriber(subscriber_with_consent, seq)

        result = evaluate_sequences()
        assert result.emails_skipped == 1

        tag = Tag.objects.create(name="vip", display_name="VIP")
        subscriber_with_consent.tags.add(tag)

        result = evaluate_sequences()
        assert result.emails_sent == 1

    def test_multi_step_advancement(
        self, subscriber_with_consent: Subscriber, multi_step_sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber_with_consent, multi_step_sequence)

        result = evaluate_sequences()
        assert result.emails_sent == 1

        enrollment = SequenceEnrollment.objects.get(
            subscriber=subscriber_with_consent, sequence=multi_step_sequence,
        )
        assert enrollment.current_step.order == 2
        assert enrollment.status == SequenceEnrollment.Status.ACTIVE

    def test_creates_email_send_with_sequence_step(
        self, subscriber_with_consent: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber_with_consent, sequence)
        evaluate_sequences()

        es = EmailSend.objects.get(subscriber=subscriber_with_consent)
        assert es.sequence_step is not None
        assert es.sequence_step.sequence == sequence


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_pause_sequence(
        self, subscriber: Subscriber, subscriber2: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        enroll_subscriber(subscriber2, sequence)

        count = pause_sequence(sequence)
        assert count == 2

        sequence.refresh_from_db()
        assert sequence.is_active is False

        for enrollment in SequenceEnrollment.objects.filter(sequence=sequence):
            assert enrollment.status == SequenceEnrollment.Status.PAUSED

    def test_resume_sequence(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        pause_sequence(sequence)

        count = resume_sequence(sequence)
        assert count == 1

        sequence.refresh_from_db()
        assert sequence.is_active is True

        enrollment = SequenceEnrollment.objects.get(subscriber=subscriber, sequence=sequence)
        assert enrollment.status == SequenceEnrollment.Status.ACTIVE

    def test_resume_skips_suppressed(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        pause_sequence(sequence)

        subscriber.status = Subscriber.Status.UNSUBSCRIBED
        subscriber.save()

        count = resume_sequence(sequence)
        assert count == 0

        enrollment = SequenceEnrollment.objects.get(subscriber=subscriber, sequence=sequence)
        assert enrollment.status == SequenceEnrollment.Status.CANCELLED


# ---------------------------------------------------------------------------
# Auto-cancel on unsubscribe
# ---------------------------------------------------------------------------


class TestAutoCancelOnUnsubscribe:
    def test_global_unsubscribe_cancels_enrollments(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        assert SequenceEnrollment.objects.filter(
            subscriber=subscriber, status=SequenceEnrollment.Status.ACTIVE,
        ).count() == 1

        process_global_unsubscribe(subscriber, method="link")

        assert SequenceEnrollment.objects.filter(
            subscriber=subscriber, status=SequenceEnrollment.Status.CANCELLED,
        ).count() == 1

    def test_webhook_bounce_cancels_enrollments(
        self, subscriber: Subscriber, sequence: Sequence, email_type: EmailType,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        EmailSend.objects.create(
            subscriber=subscriber,
            email_type=email_type,
            provider_message_id="msg_sequence_bounce",
            status=EmailSend.Status.SENT,
            subject_line_used="Welcome",
        )

        process_resend_event({
            "type": "email.bounced",
            "data": {"email_id": "msg_sequence_bounce", "to": subscriber.email},
        })

        enrollment = SequenceEnrollment.objects.get(subscriber=subscriber, sequence=sequence)
        assert enrollment.status == SequenceEnrollment.Status.CANCELLED


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class TestTriggers:
    def test_subscriber_created_trigger_matches_all(
        self, sequence: Sequence,
    ) -> None:
        sub = Subscriber.objects.create(
            email="new@example.com", name="New", status=Subscriber.Status.ACTIVE,
        )
        ConsentRecord.objects.create(
            subscriber=sub, action=ConsentRecord.Action.GRANT, method="test",
        )
        matched = trigger_sequences_for_subscriber_created(sub)
        assert len(matched) == 1
        assert SequenceEnrollment.objects.filter(subscriber=sub).count() == 1

    def test_subscriber_created_trigger_source_filter(
        self, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        seq = Sequence.objects.create(
            name="Signup Only", slug="signup-only",
            trigger_type=Sequence.TriggerType.SUBSCRIBER_CREATED,
            trigger_config={"source": "signup_form"},
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=0,
            email_type=email_type, template=template,
        )

        sub_manual = Subscriber.objects.create(
            email="manual@example.com", name="Manual",
            status=Subscriber.Status.ACTIVE, source="manual",
        )
        matched = trigger_sequences_for_subscriber_created(sub_manual)
        assert len(matched) == 0

        sub_signup = Subscriber.objects.create(
            email="signup@example.com", name="Signup",
            status=Subscriber.Status.ACTIVE, source="signup_form",
        )
        matched = trigger_sequences_for_subscriber_created(sub_signup)
        assert len(matched) == 1

    def test_tag_added_trigger(
        self, subscriber: Subscriber, multi_step_sequence: Sequence,
    ) -> None:
        matched = trigger_sequences_for_tag_added(subscriber, "pro")
        assert len(matched) == 1
        assert SequenceEnrollment.objects.filter(subscriber=subscriber).count() == 1

    def test_tag_added_trigger_no_match(
        self, subscriber: Subscriber, multi_step_sequence: Sequence,
    ) -> None:
        matched = trigger_sequences_for_tag_added(subscriber, "basic")
        assert len(matched) == 0

    def test_tag_added_trigger_single_tag_config(
        self, subscriber: Subscriber, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        seq = Sequence.objects.create(
            name="VIP", slug="vip-flow",
            trigger_type=Sequence.TriggerType.TAG_ADDED,
            trigger_config={"tag": "vip"},
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=0,
            email_type=email_type, template=template,
        )

        matched = trigger_sequences_for_tag_added(subscriber, "vip")
        assert matched == [seq]
        assert SequenceEnrollment.objects.filter(subscriber=subscriber, sequence=seq).exists()

    def test_tag_added_trigger_empty_config_matches_all(
        self, subscriber: Subscriber, email_type: EmailType, template: EmailTemplate,
    ) -> None:
        seq = Sequence.objects.create(
            name="Any Tag", slug="any-tag",
            trigger_type=Sequence.TriggerType.TAG_ADDED,
            trigger_config={},
        )
        SequenceStep.objects.create(
            sequence=seq, order=1, delay_hours=0,
            email_type=email_type, template=template,
        )

        matched = trigger_sequences_for_tag_added(subscriber, "anything")
        assert matched == [seq]

    def test_tag_subscriber_service_fires_trigger(
        self, subscriber: Subscriber, multi_step_sequence: Sequence,
    ) -> None:
        tag_subscriber(subscriber, "pro")
        assert SequenceEnrollment.objects.filter(
            subscriber=subscriber, sequence=multi_step_sequence,
        ).count() == 1

    def test_add_subscriber_fires_trigger(
        self, sequence: Sequence,
    ) -> None:
        sub = add_subscriber(
            "trigger-test@example.com",
            name="Trigger Test",
            source="manual",
        )
        # add_subscriber with double opt-in creates PENDING. The sequence
        # trigger fires after confirmation, when the subscriber becomes ACTIVE.
        from apps.consent.services import confirm_double_optin

        confirm_double_optin(sub)

        assert SequenceEnrollment.objects.filter(subscriber=sub, sequence=sequence).exists()

    def test_trigger_does_not_re_enroll(
        self, subscriber: Subscriber, multi_step_sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, multi_step_sequence)
        trigger_sequences_for_tag_added(subscriber, "pro")
        assert SequenceEnrollment.objects.filter(
            subscriber=subscriber, sequence=multi_step_sequence,
        ).count() == 1


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class TestEvaluateSequencesCommand:
    def test_runs_successfully(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        out = StringIO()
        call_command("evaluate_sequences", stdout=out)
        output = out.getvalue()
        assert "sent" in output.lower() or "Processed" in output

    def test_output_with_no_enrollments(self) -> None:
        out = StringIO()
        call_command("evaluate_sequences", stdout=out)
        output = out.getvalue()
        assert "Processed" in output


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


class TestCLISequences:
    def test_list_json(self, sequence: Sequence) -> None:
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sequences", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]["sequences"]) >= 1

    def test_status_json(self, sequence: Sequence) -> None:
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sequences", "status", "onboarding", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["slug"] == "onboarding"

    def test_enroll(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main, ["sequences", "enroll", "ada@example.com", "onboarding", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_enroll_suppressed(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        from click.testing import CliRunner

        from cli.cli import main

        subscriber.status = Subscriber.Status.UNSUBSCRIBED
        subscriber.save()

        runner = CliRunner()
        result = runner.invoke(
            main, ["sequences", "enroll", "ada@example.com", "onboarding", "--json"]
        )
        assert result.exit_code == 1

    def test_pause_and_resume(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        from click.testing import CliRunner

        from cli.cli import main

        enroll_subscriber(subscriber, sequence)

        runner = CliRunner()
        result = runner.invoke(main, ["sequences", "pause", "onboarding", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True

        result = runner.invoke(main, ["sequences", "resume", "onboarding", "--json"])
        assert result.exit_code == 0

    def test_list_human_readable(self, sequence: Sequence) -> None:
        from click.testing import CliRunner

        from cli.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["sequences", "list"])
        assert result.exit_code == 0
        assert "Onboarding" in result.output


# ---------------------------------------------------------------------------
# Sequence views
# ---------------------------------------------------------------------------


class TestSequenceViews:
    def test_sequence_list_requires_login(self, client: Client) -> None:
        response = client.get("/campaigns/sequences/")
        assert response.status_code == 302

    def test_sequence_list_page(
        self, client_logged: Client, sequence: Sequence,
    ) -> None:
        response = client_logged.get("/campaigns/sequences/")
        assert response.status_code == 200
        assert "Onboarding" in response.content.decode()

    def test_sequence_create(
        self, client_logged: Client, email_type: EmailType,
    ) -> None:
        response = client_logged.post(
            "/campaigns/sequences/new/",
            {
                "name": "New Seq",
                "slug": "new-seq",
                "trigger_type": "manual",
                "trigger_config": "{}",
            },
        )
        assert response.status_code == 302
        assert Sequence.objects.filter(slug="new-seq").exists()

    def test_sequence_detail(
        self, client_logged: Client, sequence: Sequence,
    ) -> None:
        response = client_logged.get(f"/campaigns/sequences/{sequence.pk}/")
        assert response.status_code == 200
        assert "Onboarding" in response.content.decode()

    def test_sequence_edit(
        self, client_logged: Client, sequence: Sequence,
    ) -> None:
        response = client_logged.post(
            f"/campaigns/sequences/{sequence.pk}/edit/",
            {
                "name": "Updated",
                "slug": "onboarding",
                "trigger_type": "subscriber_created",
                "trigger_config": "{}",
                "is_active": True,
            },
        )
        assert response.status_code == 302
        sequence.refresh_from_db()
        assert sequence.name == "Updated"

    def test_sequence_step_create(
        self, client_logged: Client, sequence: Sequence,
        email_type: EmailType, template2: EmailTemplate,
    ) -> None:
        response = client_logged.post(
            f"/campaigns/sequences/{sequence.pk}/steps/new/",
            {
                "delay_hours": 24,
                "email_type": email_type.pk,
                "template": template2.pk,
                "subject_override": "Day 2",
            },
        )
        assert response.status_code == 302
        assert sequence.steps.count() == 2

    def test_sequence_step_delete(
        self, client_logged: Client, sequence: Sequence,
        email_type: EmailType, template2: EmailTemplate,
    ) -> None:
        step2 = SequenceStep.objects.create(
            sequence=sequence, order=2, delay_hours=24,
            email_type=email_type, template=template2,
        )
        response = client_logged.post(
            f"/campaigns/sequences/{sequence.pk}/steps/{step2.pk}/delete/",
        )
        assert response.status_code == 302
        assert sequence.steps.count() == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_evaluate_inactive_sequence_not_processed(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        sequence.is_active = False
        sequence.save()

        result = evaluate_sequences()
        assert result.enrollments_processed == 0

    def test_cancel_enrollments_for_subscriber(
        self, subscriber: Subscriber, sequence: Sequence,
        multi_step_sequence: Sequence,
    ) -> None:
        enroll_subscriber(subscriber, sequence)
        enroll_subscriber(subscriber, multi_step_sequence)

        count = cancel_enrollments_for_subscriber(subscriber)
        assert count == 2

    def test_cancel_enrollments_for_subscriber_cancels_paused(
        self, subscriber: Subscriber, sequence: Sequence,
    ) -> None:
        enrollment = enroll_subscriber(subscriber, sequence)
        enrollment.status = SequenceEnrollment.Status.PAUSED
        enrollment.save()

        count = cancel_enrollments_for_subscriber(subscriber)
        enrollment.refresh_from_db()

        assert count == 1
        assert enrollment.status == SequenceEnrollment.Status.CANCELLED

    def test_empty_sequence_completes_immediately(
        self, subscriber: Subscriber,
    ) -> None:
        seq = Sequence.objects.create(
            name="Empty", slug="empty",
            trigger_type=Sequence.TriggerType.MANUAL,
        )
        enrollment = SequenceEnrollment.objects.create(
            subscriber=subscriber, sequence=seq, current_step=None,
        )
        result = evaluate_sequences()
        enrollment.refresh_from_db()
        assert enrollment.status == SequenceEnrollment.Status.COMPLETED
        assert result.enrollments_completed == 1
