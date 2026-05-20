"""Subscriber views for Postino.

All dashboard views require staff/admin login. Provides subscriber list,
detail, CSV import, and bulk actions (tag, export, suppress). The list view
supports HTMX partial rendering for filtered/paginated table updates.
"""

import csv
import io
import json
from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from apps.consent.models import ConsentRecord, UnsubscribeEvent
from apps.subscribers.forms import BulkSuppressForm, BulkTagForm, SubscriberImportForm
from apps.subscribers.models import Subscriber, Tag
from apps.subscribers.services import (
    SuppressedSubscriberError,
    add_subscriber,
    suppress_subscriber,
    tag_subscriber,
)


def _parse_bulk_subscriber_ids(request: HttpRequest, ids_raw: str = "") -> list[str]:
    selected = request.POST.getlist("selected") or request.GET.getlist("selected")
    if selected:
        return selected

    try:
        ids = json.loads(ids_raw or "[]")
    except (json.JSONDecodeError, TypeError):
        raise ValueError("Invalid subscriber IDs") from None

    if not isinstance(ids, list):
        raise ValueError("Invalid subscriber IDs")
    return [str(sid) for sid in ids]


def _subscriber_list_qs(request: HttpRequest):
    """Build the filtered queryset for subscriber list based on GET params."""
    qs = Subscriber.objects.prefetch_related("tags").all()

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(name__icontains=q))

    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)

    tag = request.GET.get("tag", "").strip()
    if tag:
        qs = qs.filter(tags__name=tag).distinct()

    health_below = request.GET.get("health_below", "").strip()
    if health_below:
        try:
            threshold = int(health_below)
            qs = qs.filter(health_score__lt=threshold)
        except ValueError:
            pass

    return qs.order_by("-created_at")


@staff_member_required
def subscriber_list(request: HttpRequest) -> HttpResponse:
    """Render the subscriber list page with filters and pagination.

    Supports HTMX partial requests: when ``HX-Request`` is present,
    returns only the ``_table.html`` partial instead of the full page.
    """
    qs = _subscriber_list_qs(request)
    page_number = request.GET.get("page", 1)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)

    # Evaluate to lists to avoid RecursionError in Django's test client
    # context copying with lazy related managers (tags.all).
    subscriber_list = list(page_obj.object_list)

    all_tags = list(Tag.objects.all().order_by("name"))
    all_statuses = Subscriber.Status.choices

    # Build preserved filter querystring for pagination links.
    filter_params = {}
    for key in ("q", "status", "tag", "health_below"):
        val = request.GET.get(key, "").strip()
        if val:
            filter_params[key] = val
    filter_qs = urlencode(filter_params)

    context = {
        "nav_active": "subscribers",
        "subscribers": subscriber_list,
        "page_obj": page_obj,
        "all_tags": all_tags,
        "all_statuses": all_statuses,
        "current_q": request.GET.get("q", ""),
        "current_status": request.GET.get("status", ""),
        "current_tag": request.GET.get("tag", ""),
        "current_health_below": request.GET.get("health_below", ""),
        "filter_qs": filter_qs,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "subscribers/_table.html", context)

    return render(request, "subscribers/list.html", context)


@staff_member_required
def subscriber_detail(request: HttpRequest, subscriber_id: str) -> HttpResponse:
    """Render the subscriber detail page with profile, consent, and events."""
    subscriber = get_object_or_404(Subscriber, id=subscriber_id)
    consent_records = (
        ConsentRecord.objects.filter(subscriber=subscriber)
        .select_related("email_type")
        .order_by("-created_at")[:50]
    )
    unsubscribe_events = (
        UnsubscribeEvent.objects.filter(subscriber=subscriber)
        .select_related("email_type")
        .order_by("-created_at")[:20]
    )

    context = {
        "nav_active": "subscribers",
        "subscriber": subscriber,
        "consent_records": consent_records,
        "unsubscribe_events": unsubscribe_events,
        "subscriber_tags": list(subscriber.tags.all()),
    }
    return render(request, "subscribers/detail.html", context)


@staff_member_required
def subscriber_import(request: HttpRequest) -> HttpResponse:
    """Handle CSV file upload for bulk subscriber import.

    Uses ``add_subscriber()`` for each row to preserve suppression
    checks and double opt-in logic. Reports created/skipped/suppressed/
    error counts without crashing on individual row failures.
    """
    if request.method != "POST":
        context = {
            "nav_active": "subscribers",
            "form": SubscriberImportForm(),
        }
        return render(request, "subscribers/import.html", context)

    form = SubscriberImportForm(request.POST, request.FILES)
    if not form.is_valid():
        context = {
            "nav_active": "subscribers",
            "form": form,
        }
        return render(request, "subscribers/import.html", context)

    csv_file = form.cleaned_data["csv_file"]
    default_tag = form.cleaned_data.get("default_tag", "").strip()
    tag_names = [default_tag] if default_tag else None

    created = 0
    skipped = 0
    suppressed = 0
    errors: list[str] = []

    decoded = csv_file.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))

    if not reader.fieldnames or "email" not in reader.fieldnames:
        form.add_error("csv_file", _("CSV file must have an 'email' column."))
        context = {
            "nav_active": "subscribers",
            "form": form,
        }
        return render(request, "subscribers/import.html", context)

    for row_num, row in enumerate(reader, start=2):
        email = row.get("email", "").strip()
        if not email:
            errors.append(_("Row %(row_num)s: empty email, skipped.") % {"row_num": row_num})
            continue

        name = row.get("name", "").strip()
        row_tag = row.get("tag", "").strip()
        row_tag_names = [row_tag] if row_tag else tag_names

        normalized_email = email.strip().lower()
        already_exists = Subscriber.objects.filter(email=normalized_email).exists()

        try:
            sub = add_subscriber(
                email=email,
                name=name,
                source="import",
                tag_names=row_tag_names,
            )
            if row_tag_names:
                for t in row_tag_names:
                    tag_subscriber(sub, t)
            if already_exists:
                skipped += 1
            else:
                created += 1
        except SuppressedSubscriberError:
            suppressed += 1
        except Exception as exc:
            errors.append(
                _("Row %(row_num)s (%(email)s): %(error)s")
                % {"row_num": row_num, "email": email, "error": str(exc)}
            )

    context = {
        "nav_active": "subscribers",
        "form": SubscriberImportForm(),
        "import_results": {
            "created": created,
            "skipped": skipped,
            "suppressed": suppressed,
            "errors": errors,
            "total_processed": created + skipped + suppressed + len(errors),
        },
    }
    return render(request, "subscribers/import.html", context)


@staff_member_required
def subscriber_bulk_tag(request: HttpRequest) -> HttpResponse:
    """Bulk-tag selected subscribers via server-side form POST."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    form = BulkTagForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))

    tag_name = form.cleaned_data["tag"]
    ids_raw = form.cleaned_data["subscriber_ids"]

    try:
        ids = _parse_bulk_subscriber_ids(request, ids_raw)
    except ValueError:
        return HttpResponseBadRequest("Invalid subscriber IDs")

    tagged_count = 0
    for sid in ids:
        try:
            subscriber = Subscriber.objects.get(id=sid)
        except (Subscriber.DoesNotExist, ValueError):
            continue
        if not subscriber.is_suppressed:
            tag_subscriber(subscriber, tag_name)
            tagged_count += 1

    return redirect("subscribers:list")


@staff_member_required
def subscriber_bulk_suppress(request: HttpRequest) -> HttpResponse:
    """Bulk-suppress selected subscribers via server-side form POST."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    form = BulkSuppressForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest(str(form.errors))

    reason = form.cleaned_data["reason"]
    ids_raw = form.cleaned_data["subscriber_ids"]

    try:
        ids = _parse_bulk_subscriber_ids(request, ids_raw)
    except ValueError:
        return HttpResponseBadRequest("Invalid subscriber IDs")

    suppressed_count = 0
    for sid in ids:
        try:
            subscriber = Subscriber.objects.get(id=sid)
        except (Subscriber.DoesNotExist, ValueError):
            continue
        if not subscriber.is_suppressed:
            suppress_subscriber(subscriber, reason=reason)
            suppressed_count += 1

    return redirect("subscribers:list")


@staff_member_required
def subscriber_bulk_export(request: HttpRequest) -> HttpResponse:
    """Export selected subscribers as CSV download."""
    ids_raw = (
        request.POST.get("subscriber_ids", "[]")
        if request.method == "POST"
        else request.GET.get("subscriber_ids", "[]")
    )

    try:
        ids = _parse_bulk_subscriber_ids(request, ids_raw)
    except ValueError:
        return HttpResponseBadRequest("Invalid subscriber IDs")

    subscribers = Subscriber.objects.filter(id__in=ids).prefetch_related("tags")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="subscribers_export.csv"'

    writer = csv.writer(response)
    writer.writerow(["email", "name", "status", "source", "health_score", "tags"])

    for sub in subscribers:
        tags = ", ".join(sub.tags.values_list("name", flat=True))
        writer.writerow([sub.email, sub.name, sub.status, sub.source, sub.health_score, tags])

    return response
