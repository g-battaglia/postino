"""Webhook endpoint views for receiving provider event notifications.

Each provider gets its own URL. The Resend endpoint verifies Svix-style
signatures before processing. All endpoints are CSRF-exempt (they receive
POST requests from external services, not from browser forms).
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .signature import SignatureVerificationError, verify_signature

logger = logging.getLogger(__name__)


def _get_webhook_secret() -> str:
    """Retrieve the Resend webhook signing secret from Django settings."""
    from django.conf import settings

    return getattr(settings, "POSTINO_RESEND_WEBHOOK_SIGNING_SECRET", "")


@csrf_exempt
@require_POST
def resend_webhook(request: HttpRequest) -> HttpResponse:
    """Handle inbound Resend webhook events.

    Verifies the Svix signature, persists the event, and triggers
    processing (status updates, auto-suppression on bounce/complaint).
    """
    secret = _get_webhook_secret()
    if not secret:
        logger.error("Resend webhook received but no signing secret is configured.")
        return JsonResponse(
            {"error": "Webhook signing secret not configured."},
            status=503,
        )

    body = request.body.decode("utf-8")

    svix_id = request.headers.get("svix-id", "")
    svix_timestamp = request.headers.get("svix-timestamp", "")
    svix_signature = request.headers.get("svix-signature", "")

    try:
        verify_signature(
            body=body,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
            svix_signature=svix_signature,
            secret=secret,
        )
    except SignatureVerificationError as exc:
        logger.warning("Resend webhook signature verification failed: %s", exc)
        return JsonResponse({"error": "Invalid signature."}, status=400)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("Resend webhook received invalid JSON body.")
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    from .services import process_resend_event

    try:
        process_resend_event(payload)
    except Exception:
        logger.exception("Error processing Resend webhook event.")
        return JsonResponse({"error": "Processing error."}, status=500)

    return JsonResponse({"ok": True}, status=200)
