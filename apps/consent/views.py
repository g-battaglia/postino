"""Public unsubscribe and preference center views.

Both unsubscribe endpoints are unauthenticated and synchronous.
The manual form is CSRF-protected. The one-click endpoint is
CSRF-exempt because mail providers POST to it without a token (RFC 8058).

The preference center is also unauthenticated and CSRF-protected on POST.
"""

from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from apps.consent.forms import PreferenceForm, UnsubscribeForm
from apps.consent.models import ConsentRecord, EmailType
from apps.consent.services import (
    confirm_double_optin,
    get_latest_consent_action,
    process_gdpr_deletion,
    process_global_unsubscribe,
    process_per_type_unsubscribe,
)
from apps.consent.tokens import InvalidToken, verify_double_optin_token, verify_unsubscribe_token
from apps.subscribers.models import Subscriber


def _get_client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _resolve_subscriber(token: str, email_type_slug: str | None) -> Subscriber:
    """Verify token and return the subscriber. Raises InvalidToken on failure."""
    subscriber_id = verify_unsubscribe_token(token, email_type_slug)
    try:
        return Subscriber.objects.get(pk=subscriber_id)
    except Subscriber.DoesNotExist as exc:
        raise InvalidToken("Subscriber not found.") from exc


def _build_form_context(
    request: HttpRequest,
    token: str,
    email_type_slug: str | None,
) -> dict | None:
    """Validate token and build template context for the form page.

    Returns None (caller should return 400) on token errors.
    """
    if not token:
        return None
    try:
        subscriber = _resolve_subscriber(token, email_type_slug)
    except (InvalidToken, Subscriber.DoesNotExist):
        return None

    email_type_obj = None
    if email_type_slug:
        try:
            email_type_obj = EmailType.objects.get(slug=email_type_slug, is_active=True)
        except EmailType.DoesNotExist:
            pass

    has_email_type = email_type_obj is not None
    email_type_name = email_type_obj.name if email_type_obj else ""
    initial = {
        "token": token,
        "email_type_slug": email_type_slug or "",
        "action": (
            UnsubscribeForm.CHOICES_PER_TYPE if has_email_type
            else UnsubscribeForm.CHOICES_GLOBAL
        ),
    }
    form = UnsubscribeForm(
        initial=initial,
        has_email_type=has_email_type,
        email_type_name=email_type_name,
    )

    return {
        "form": form,
        "subscriber": subscriber,
        "email_type": email_type_obj,
        "has_email_type": has_email_type,
    }


# ---------------------------------------------------------------------------
# Manual unsubscribe (GET renders form, POST processes -- CSRF-protected)
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def unsubscribe_view(request: HttpRequest) -> HttpResponse:
    """Handle the manual unsubscribe form (GET renders, POST processes)."""
    token = request.GET.get("token", "").strip() or request.POST.get("token", "").strip()
    email_type_slug = request.GET.get("type", "").strip() or None

    if request.method == "GET":
        context = _build_form_context(request, token, email_type_slug)
        if context is None:
            ctx = {
                "title": _("Invalid token"),
                "message": _("This unsubscribe link is invalid or has expired."),
            }
            return render(request, "consent/error.html", ctx, status=400)
        return render(request, "consent/unsubscribe.html", context)

    # POST -- CSRF middleware handles token validation before we get here.
    has_type = bool(request.POST.get("email_type_slug", "").strip())
    form = UnsubscribeForm(request.POST, has_email_type=has_type)
    if not form.is_valid():
        return HttpResponseBadRequest(_("Invalid form submission."))

    token = form.cleaned_data["token"]
    email_type_slug = form.cleaned_data.get("email_type_slug") or None
    action = form.cleaned_data["action"]

    try:
        subscriber = _resolve_subscriber(token, email_type_slug)
    except (InvalidToken, Subscriber.DoesNotExist):
        return HttpResponseBadRequest(_("Invalid or expired token."))

    ip_address = _get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    if action == UnsubscribeForm.CHOICES_PER_TYPE:
        if not email_type_slug:
            return HttpResponseBadRequest(_("No email type specified."))
        try:
            email_type_obj = EmailType.objects.get(slug=email_type_slug)
        except EmailType.DoesNotExist:
            return HttpResponseBadRequest(_("Unknown email type."))
        process_per_type_unsubscribe(
            subscriber, email_type_obj,
            ip_address=ip_address, user_agent=user_agent, method="link",
        )
    elif action == UnsubscribeForm.CHOICES_GLOBAL:
        process_global_unsubscribe(
            subscriber,
            ip_address=ip_address, user_agent=user_agent, method="link",
        )
    elif action == UnsubscribeForm.CHOICES_DELETION:
        process_gdpr_deletion(
            subscriber,
            ip_address=ip_address, user_agent=user_agent, method="gdpr_deletion",
        )
    else:
        return HttpResponseBadRequest(_("Unknown action."))

    return render(request, "consent/unsubscribe_done.html", {"action": action})


# ---------------------------------------------------------------------------
# RFC 8058 one-click (CSRF-exempt)
# ---------------------------------------------------------------------------


@csrf_exempt
@require_POST
def unsubscribe_one_click(request: HttpRequest) -> HttpResponse:
    """Process an RFC 8058 one-click unsubscribe POST.

    Mail providers POST to this endpoint with the body
    ``List-Unsubscribe=One-Click``. No CSRF token is available because
    this is an automated protocol, not a user-initiated form submission.

    Accepts two forms:
    - Form-encoded POST where ``request.POST["List-Unsubscribe"] == "One-Click"``
    - Exact raw body ``List-Unsubscribe=One-Click`` (no extra text).

    Always processes as a global unsubscribe using an unscoped token.
    """
    token = request.GET.get("token", "").strip()
    if not token:
        return HttpResponseBadRequest(_("Missing token."))

    if not _is_valid_one_click_body(request):
        return HttpResponseBadRequest(_("Invalid one-click marker."))

    try:
        subscriber = _resolve_subscriber(token, email_type_slug=None)
    except (InvalidToken, Subscriber.DoesNotExist):
        return HttpResponseBadRequest(_("Invalid or expired token."))

    ip_address = _get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    process_global_unsubscribe(
        subscriber,
        ip_address=ip_address, user_agent=user_agent, method="one_click",
    )

    return HttpResponse(status=200)


def _is_valid_one_click_body(request: HttpRequest) -> bool:
    """Return True if the request body is a valid RFC 8058 one-click marker.

    Accepts:
    - Form-encoded POST with ``List-Unsubscribe=One-Click`` as the only key.
    - Exact raw body ``List-Unsubscribe=One-Click`` (no other content).
    """
    if (request.POST.get("List-Unsubscribe") == "One-Click"
            and len(request.POST) == 1):
        return True

    body = request.body.decode("utf-8", errors="replace").strip()
    return body == "List-Unsubscribe=One-Click"


# ---------------------------------------------------------------------------
# Double opt-in email confirmation (GET only, no CSRF required)
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
def confirm_email_view(request: HttpRequest) -> HttpResponse:
    """Confirm a subscriber's email address via double opt-in token.

    The subscriber receives a link with a signed token.  Visiting the link
    transitions the subscriber from PENDING to ACTIVE and records a
    consent grant.  No JavaScript or CSRF token is required.
    """
    token = request.GET.get("token", "").strip()
    if not token:
        return render(request, "consent/confirm_email.html", {"success": False})

    try:
        subscriber_id = verify_double_optin_token(token)
    except InvalidToken:
        return render(request, "consent/confirm_email.html", {"success": False})

    try:
        subscriber = Subscriber.objects.get(pk=subscriber_id)
    except Subscriber.DoesNotExist:
        return render(request, "consent/confirm_email.html", {"success": False})

    if subscriber.double_optin_token != token:
        return render(request, "consent/confirm_email.html", {"success": False})

    confirm_double_optin(subscriber)

    return render(request, "consent/confirm_email.html", {"success": True})


# ---------------------------------------------------------------------------
# Preference center (GET renders, POST updates -- CSRF-protected)
# ---------------------------------------------------------------------------


def _resolve_subscriber_unscoped(token: str) -> Subscriber:
    """Verify an unscoped token and return the subscriber."""
    subscriber_id = verify_unsubscribe_token(token)
    try:
        return Subscriber.objects.get(pk=subscriber_id)
    except Subscriber.DoesNotExist as exc:
        raise InvalidToken("Subscriber not found.") from exc


def _build_preference_context(
    subscriber: Subscriber,
    token: str,
) -> dict:
    """Build template context for the preference center page."""
    email_types = list(EmailType.objects.filter(is_active=True).order_by("slug"))
    consent_state: dict[str, str | None] = {}
    for et in email_types:
        action = get_latest_consent_action(subscriber, et)
        consent_state[et.slug] = action
        et.has_consent = action == ConsentRecord.Action.GRANT

    is_suppressed = subscriber.status in (
        Subscriber.Status.UNSUBSCRIBED,
        Subscriber.Status.BOUNCED,
        Subscriber.Status.COMPLAINED,
        Subscriber.Status.DELETED,
    )

    form = PreferenceForm(token=token)

    return {
        "subscriber": subscriber,
        "email_types": email_types,
        "consent_state": consent_state,
        "is_suppressed": is_suppressed,
        "form": form,
        "token": token,
    }


@require_http_methods(["GET", "POST"])
def preferences_view(request: HttpRequest) -> HttpResponse:
    """Handle the public preference center (GET renders, POST processes)."""
    token = request.GET.get("token", "").strip() or request.POST.get("token", "").strip()
    if not token:
        return HttpResponseBadRequest(_("Invalid or missing token."))

    try:
        subscriber = _resolve_subscriber_unscoped(token)
    except (InvalidToken, Subscriber.DoesNotExist):
        return HttpResponseBadRequest(_("Invalid or expired token."))

    if request.method == "GET":
        context = _build_preference_context(subscriber, token)
        return render(request, "consent/preferences.html", context)

    # POST
    form = PreferenceForm(request.POST, token=token)
    if not form.is_valid():
        return HttpResponseBadRequest(_("Invalid form submission."))

    token = form.cleaned_data["token"]
    global_action = form.cleaned_data.get("global_action") or ""

    ip_address = _get_client_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    if global_action == PreferenceForm.GLOBAL_ACTION_UNSUBSCRIBE:
        process_global_unsubscribe(
            subscriber,
            ip_address=ip_address,
            user_agent=user_agent,
            method="preference_center",
        )
        return render(request, "consent/preferences_done.html", {
            "action": "global",
            "subscriber_email": subscriber.email,
        })

    if global_action == PreferenceForm.GLOBAL_ACTION_DELETION:
        process_gdpr_deletion(
            subscriber,
            ip_address=ip_address,
            user_agent=user_agent,
            method="preference_center_deletion",
        )
        return render(request, "consent/preferences_done.html", {
            "action": "deletion",
            "subscriber_email": subscriber.email,
        })

    # Per-type preference updates: only for active (non-suppressed) subscribers.
    if subscriber.status != Subscriber.Status.ACTIVE:
        context = _build_preference_context(subscriber, token)
        context["message"] = _(
            "Your account is not active. Per-type preferences cannot be changed."
        )
        return render(request, "consent/preferences.html", context)

    email_types = list(EmailType.objects.filter(is_active=True).order_by("slug"))

    with transaction.atomic():
        for et in email_types:
            field_name = f"type_{et.slug}"
            is_checked = request.POST.get(field_name) == "on"
            latest = get_latest_consent_action(subscriber, et)

            if is_checked and latest != ConsentRecord.Action.GRANT:
                ConsentRecord.objects.create(
                    subscriber=subscriber,
                    email_type=et,
                    action=ConsentRecord.Action.GRANT,
                    method="preference_center",
                    ip_address=ip_address,
                )
            elif not is_checked and latest == ConsentRecord.Action.GRANT:
                ConsentRecord.objects.create(
                    subscriber=subscriber,
                    email_type=et,
                    action=ConsentRecord.Action.WITHDRAW,
                    method="preference_center",
                    ip_address=ip_address,
                )

    context = _build_preference_context(subscriber, token)
    context["message"] = _("Your preferences have been updated.")
    return render(request, "consent/preferences.html", context)


# ---------------------------------------------------------------------------
# GDPR admin endpoints (staff-only)
# ---------------------------------------------------------------------------


@staff_member_required
@require_http_methods(["GET", "POST"])
def gdpr_data_export(request: HttpRequest, subscriber_id: str) -> HttpResponse:
    """Admin endpoint to export all personal data for a subscriber as JSON.

    GET renders a confirmation page. POST generates and downloads the JSON.
    Staff-only, synchronous, no REST API.
    """
    subscriber = get_object_or_404(Subscriber, id=subscriber_id)

    if request.method == "POST":
        from apps.subscribers.services import export_subscriber_data

        data = export_subscriber_data(subscriber)
        response = JsonResponse(data, json_dumps_params={"indent": 2, "default": str})
        filename = f"postino-gdpr-export-{subscriber.email}.json"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    context = {
        "nav_active": "subscribers",
        "subscriber": subscriber,
    }
    return render(request, "consent/gdpr_export.html", context)


@staff_member_required
@require_http_methods(["GET", "POST"])
def gdpr_data_delete(request: HttpRequest, subscriber_id: str) -> HttpResponse:
    """Admin endpoint to initiate GDPR Art. 17 data deletion for a subscriber.

    GET renders a confirmation page. POST processes the deletion.
    Staff-only, synchronous, no REST API.
    """
    subscriber = get_object_or_404(Subscriber, id=subscriber_id)

    if request.method == "POST":
        process_gdpr_deletion(subscriber, method="gdpr_deletion_admin")
        return render(request, "consent/gdpr_delete_done.html", {"subscriber": subscriber})

    context = {
        "nav_active": "subscribers",
        "subscriber": subscriber,
    }
    return render(request, "consent/gdpr_delete.html", context)
