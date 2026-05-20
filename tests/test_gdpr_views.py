"""Tests for GDPR data export and deletion web endpoints.

Covers:
- Staff-only access enforcement
- Data export GET renders confirmation page
- Data export POST returns JSON download
- Data deletion GET renders confirmation page
- Data deletion POST processes GDPR deletion
- Non-staff users are redirected
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.consent.models import UnsubscribeEvent
from apps.subscribers.models import Subscriber


class TestGDPRExportView(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.admin = User.objects.create_superuser("admin", "admin@test.com", "password")
        self.subscriber = Subscriber.objects.create(
            email="export@test.com", status="active", name="Export User"
        )

    def test_requires_login(self) -> None:
        url = f"/gdpr/{self.subscriber.id}/export/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_get_renders_confirmation_page(self) -> None:
        self.client.login(username="admin", password="password")
        url = f"/gdpr/{self.subscriber.id}/export/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "export@test.com")
        self.assertContains(response, "Download JSON")

    def test_post_returns_json_download(self) -> None:
        self.client.login(username="admin", password="password")
        url = f"/gdpr/{self.subscriber.id}/export/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertContains(response, "export@test.com")

    def test_404_for_nonexistent_subscriber(self) -> None:
        self.client.login(username="admin", password="password")
        import uuid
        url = f"/gdpr/{uuid.uuid4()}/export/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


class TestGDPRDeleteView(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.admin = User.objects.create_superuser("admin", "admin@test.com", "password")
        self.subscriber = Subscriber.objects.create(
            email="delete@test.com", status="active", name="Delete User"
        )

    def test_requires_login(self) -> None:
        url = f"/gdpr/{self.subscriber.id}/delete/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_get_renders_confirmation_page(self) -> None:
        self.client.login(username="admin", password="password")
        url = f"/gdpr/{self.subscriber.id}/delete/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "delete@test.com")
        self.assertContains(response, "Delete all data")

    def test_post_processes_deletion(self) -> None:
        self.client.login(username="admin", password="password")
        url = f"/gdpr/{self.subscriber.id}/delete/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Personal data has been deleted")

        self.subscriber.refresh_from_db()
        self.assertEqual(self.subscriber.status, "deleted")
        self.assertEqual(self.subscriber.name, "")

    def test_creates_unsubscribe_event(self) -> None:
        self.client.login(username="admin", password="password")
        url = f"/gdpr/{self.subscriber.id}/delete/"
        self.client.post(url)

        self.assertEqual(
            UnsubscribeEvent.objects.filter(email="delete@test.com").count(), 1
        )

    def test_non_staff_cannot_access(self) -> None:
        User.objects.create_user("user", "user@test.com", "password")
        self.client.login(username="user", password="password")
        url = f"/gdpr/{self.subscriber.id}/delete/"
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)
