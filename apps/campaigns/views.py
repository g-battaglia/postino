"""Campaign and sequence views for Postino.

All dashboard views require staff/admin login. Provides campaign list with
status filter/search and pagination, campaign create/edit forms, campaign
detail page, and sequence list/create/edit/detail views with vertical
timeline editor.
"""

import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from apps.consent.models import EmailType
from apps.templates_mgr.models import EmailTemplate

from .forms import CampaignForm, SequenceForm
from .models import Campaign, EmailSend, Sequence, SequenceEnrollment, SequenceStep


@staff_member_required
def campaign_list(request: HttpRequest) -> HttpResponse:
    """Render the campaign list page with filters and pagination.

    Supports HTMX partial requests: when ``HX-Request`` is present,
    returns only the ``_table.html`` partial instead of the full page.
    """
    qs = Campaign.objects.select_related("email_type", "template").all()

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(subject_line__icontains=q))

    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at")

    page_number = request.GET.get("page", 1)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)

    status_counts = dict(
        Campaign.objects.values("status").annotate(cnt=Count("pk")).values_list("status", "cnt"),
    )
    status_pills = [
        {
            "value": sv,
            "label": sl,
            "count": status_counts.get(sv, 0),
            "active": status == sv,
        }
        for sv, sl in Campaign.Status.choices
    ]

    filter_params = {}
    for key in ("q", "status"):
        val = request.GET.get(key, "").strip()
        if val:
            filter_params[key] = val
    filter_qs = urlencode(filter_params)

    context = {
        "nav_active": "campaigns",
        "campaigns": page_obj.object_list,
        "page_obj": page_obj,
        "status_pills": status_pills,
        "current_q": request.GET.get("q", ""),
        "current_status": status,
        "filter_qs": filter_qs,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "campaigns/_table.html", context)

    return render(request, "campaigns/list.html", context)


@staff_member_required
def campaign_create(request: HttpRequest) -> HttpResponse:
    """Handle campaign creation."""
    if request.method == "POST":
        form = CampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save()
            messages.success(
                request,
                _('Campaign "%(name)s" created.') % {"name": campaign.name},
            )
            return redirect("campaigns:detail", campaign_id=campaign.pk)
    else:
        initial = {}
        email_type_id = request.GET.get("email_type")
        if email_type_id:
            initial["email_type"] = email_type_id
        template_id = request.GET.get("template")
        if template_id:
            initial["template"] = template_id
        form = CampaignForm(initial=initial)

    context = {
        "nav_active": "campaigns",
        "form": form,
        "email_types": EmailType.objects.all().order_by("name"),
        "templates": EmailTemplate.objects.all().order_by("name"),
        "is_edit": False,
    }
    return render(request, "campaigns/form.html", context)


@staff_member_required
def campaign_detail(request: HttpRequest, campaign_id: int) -> HttpResponse:
    """Render the campaign detail page with stats and recent sends."""
    campaign = get_object_or_404(Campaign, pk=campaign_id)

    email_sends = (
        EmailSend.objects.filter(campaign=campaign)
        .select_related("subscriber")
        .order_by("-sent_at")[:50]
    )

    send_stats = {
        "total": EmailSend.objects.filter(campaign=campaign).count(),
        "sent": EmailSend.objects.filter(
            campaign=campaign, status__in=[EmailSend.Status.SENT, EmailSend.Status.DELIVERED],
        ).count(),
        "delivered": EmailSend.objects.filter(
            campaign=campaign, status=EmailSend.Status.DELIVERED,
        ).count(),
        "opened": EmailSend.objects.filter(
            campaign=campaign, status__in=[EmailSend.Status.OPENED, EmailSend.Status.CLICKED],
        ).count(),
        "clicked": EmailSend.objects.filter(
            campaign=campaign, status=EmailSend.Status.CLICKED,
        ).count(),
        "bounced": EmailSend.objects.filter(
            campaign=campaign, status=EmailSend.Status.BOUNCED,
        ).count(),
        "failed": EmailSend.objects.filter(
            campaign=campaign, status=EmailSend.Status.FAILED,
        ).count(),
    }

    can_edit = campaign.status in (Campaign.Status.DRAFT, Campaign.Status.SCHEDULED)

    context = {
        "nav_active": "campaigns",
        "campaign": campaign,
        "email_sends": email_sends,
        "send_stats": send_stats,
        "can_edit": can_edit,
    }
    return render(request, "campaigns/detail.html", context)


@staff_member_required
def campaign_edit(request: HttpRequest, campaign_id: int) -> HttpResponse:
    """Handle campaign editing for draft/scheduled campaigns."""
    campaign = get_object_or_404(Campaign, pk=campaign_id)

    if campaign.status not in (Campaign.Status.DRAFT, Campaign.Status.SCHEDULED):
        messages.error(
            request,
            _(
                'Cannot edit campaign "%(name)s" — it is %(status)s. '
                "Only draft and scheduled campaigns can be edited."
            )
            % {"name": campaign.name, "status": campaign.get_status_display()},
        )
        return redirect("campaigns:detail", campaign_id=campaign.pk)

    if request.method == "POST":
        form = CampaignForm(request.POST, instance=campaign)
        if form.is_valid():
            updated = form.save()
            messages.success(
                request,
                _('Campaign "%(name)s" updated.') % {"name": updated.name},
            )
            return redirect("campaigns:detail", campaign_id=updated.pk)
    else:
        form = CampaignForm(instance=campaign)

    context = {
        "nav_active": "campaigns",
        "form": form,
        "campaign": campaign,
        "email_types": EmailType.objects.all().order_by("name"),
        "templates": EmailTemplate.objects.all().order_by("name"),
        "is_edit": True,
    }
    return render(request, "campaigns/form.html", context)


# ---------------------------------------------------------------------------
# Sequence views
# ---------------------------------------------------------------------------


@staff_member_required
def sequence_list(request: HttpRequest) -> HttpResponse:
    """Render the sequence list page."""
    qs = Sequence.objects.all()

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))

    is_active = request.GET.get("is_active", "").strip()
    if is_active == "true":
        qs = qs.filter(is_active=True)
    elif is_active == "false":
        qs = qs.filter(is_active=False)

    trigger_type = request.GET.get("trigger_type", "").strip()
    if trigger_type:
        qs = qs.filter(trigger_type=trigger_type)

    qs = qs.order_by("-created_at")

    page_number = request.GET.get("page", 1)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)

    active_count = Sequence.objects.filter(is_active=True).count()
    total_count = paginator.count

    context = {
        "nav_active": "sequences",
        "sequences": page_obj.object_list,
        "page_obj": page_obj,
        "total_count": total_count,
        "active_count": active_count,
        "current_q": request.GET.get("q", ""),
        "current_is_active": is_active,
        "current_trigger_type": trigger_type,
        "trigger_types": Sequence.TriggerType.choices,
    }
    return render(request, "sequences/list.html", context)


@staff_member_required
def sequence_create(request: HttpRequest) -> HttpResponse:
    """Handle sequence creation."""
    if request.method == "POST":
        form = SequenceForm(request.POST)
        if form.is_valid():
            sequence = form.save()
            messages.success(
                request,
                _('Sequence "%(name)s" created.') % {"name": sequence.name},
            )
            return redirect("sequences:detail", sequence_id=sequence.pk)
    else:
        form = SequenceForm()

    context = {
        "nav_active": "sequences",
        "form": form,
        "is_edit": False,
    }
    return render(request, "sequences/form.html", context)


@staff_member_required
def sequence_detail(request: HttpRequest, sequence_id: int) -> HttpResponse:
    """Render sequence detail/editor with vertical timeline of steps."""
    sequence = get_object_or_404(Sequence, pk=sequence_id)
    steps = sequence.steps.select_related("email_type", "template").all()

    enrollments = (
        SequenceEnrollment.objects.filter(sequence=sequence)
        .select_related("subscriber")
        .order_by("-enrolled_at")[:50]
    )

    enrollment_counts = dict(
        SequenceEnrollment.objects.filter(sequence=sequence)
        .values("status")
        .annotate(cnt=Count("pk"))
        .values_list("status", "cnt"),
    )

    context = {
        "nav_active": "sequences",
        "sequence": sequence,
        "steps": steps,
        "enrollments": enrollments,
        "enrollment_counts": enrollment_counts,
        "total_enrollments": sum(enrollment_counts.values()),
        "email_types": EmailType.objects.filter(is_active=True).order_by("name"),
        "templates": EmailTemplate.objects.all().order_by("name"),
    }
    return render(request, "sequences/detail.html", context)


@staff_member_required
def sequence_edit(request: HttpRequest, sequence_id: int) -> HttpResponse:
    """Handle sequence editing."""
    sequence = get_object_or_404(Sequence, pk=sequence_id)

    if request.method == "POST":
        form = SequenceForm(request.POST, instance=sequence)
        if form.is_valid():
            updated = form.save()
            messages.success(
                request,
                _('Sequence "%(name)s" updated.') % {"name": updated.name},
            )
            return redirect("sequences:detail", sequence_id=updated.pk)
    else:
        form = SequenceForm(instance=sequence)

    context = {
        "nav_active": "sequences",
        "form": form,
        "sequence": sequence,
        "is_edit": True,
    }
    return render(request, "sequences/form.html", context)


@staff_member_required
def sequence_step_create(request: HttpRequest, sequence_id: int) -> HttpResponse:
    """Add a step to a sequence."""
    sequence = get_object_or_404(Sequence, pk=sequence_id)

    if request.method == "POST":
        delay_raw = request.POST.get("delay_hours", "0")
        try:
            delay_hours = int(delay_raw)
        except (TypeError, ValueError):
            messages.error(request, _("Delay must be a whole number of hours."))
            return redirect("sequences:detail", sequence_id=sequence.pk)

        if delay_hours < 0:
            messages.error(request, _("Delay cannot be negative."))
            return redirect("sequences:detail", sequence_id=sequence.pk)

        order = sequence.steps.count() + 1
        step = SequenceStep(
            sequence=sequence,
            order=order,
            delay_hours=delay_hours,
            email_type_id=request.POST.get("email_type"),
            template_id=request.POST.get("template"),
            subject_override=request.POST.get("subject_override", ""),
        )

        condition_raw = request.POST.get("condition", "").strip()
        if condition_raw:
            try:
                condition = json.loads(condition_raw)
            except json.JSONDecodeError:
                messages.error(request, _("Invalid condition JSON."))
                return redirect("sequences:detail", sequence_id=sequence.pk)

            if not isinstance(condition, dict):
                messages.error(request, _("Condition must be a JSON object."))
                return redirect("sequences:detail", sequence_id=sequence.pk)

            step.condition = condition

        step.save()
        messages.success(request, _("Step %(order)d added.") % {"order": order})
        return redirect("sequences:detail", sequence_id=sequence.pk)

    return redirect("sequences:detail", sequence_id=sequence.pk)


@staff_member_required
def sequence_step_delete(request: HttpRequest, sequence_id: int, step_id: int) -> HttpResponse:
    """Delete a step from a sequence and re-order remaining steps."""
    sequence = get_object_or_404(Sequence, pk=sequence_id)
    step = get_object_or_404(SequenceStep, pk=step_id, sequence=sequence)

    if request.method == "POST":
        step.delete()
        remaining = sequence.steps.order_by("order")
        for idx, s in enumerate(remaining, start=1):
            if s.order != idx:
                s.order = idx
                s.save(update_fields=["order"])
        messages.success(request, _("Step removed."))
        return redirect("sequences:detail", sequence_id=sequence.pk)

    return redirect("sequences:detail", sequence_id=sequence.pk)
