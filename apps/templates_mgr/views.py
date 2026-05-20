"""Template manager views for Postino.

All dashboard views require staff/admin login. Provides template list
with search, create/edit forms, and a detail page with live preview
rendered from sample context data.
"""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from apps.campaigns.models import Campaign

from .forms import EmailTemplateForm
from .models import EmailTemplate
from .renderer import TemplateRenderError, render_saved_template

_PREVIEW_UNSUBSCRIBE_URL = "https://example.com/unsubscribe/?token=preview-token"
_PREVIEW_CONTEXT = {
    "subscriber_name": "Ada Lovelace",
    "subscriber_email": "ada@example.com",
    "unsubscribe_url": _PREVIEW_UNSUBSCRIBE_URL,
    "preferences_url": "https://example.com/preferences/?token=preview-token",
    "current_date": "2026-05-20",
}


def _render_preview(template: EmailTemplate) -> dict:
    """Attempt to render a template with sample context for preview.

    Returns a dict with 'subject', 'html', 'text' on success or
    'error' with a message on failure. Never raises.
    """
    try:
        subject, html, text = render_saved_template(template, _PREVIEW_CONTEXT)
    except TemplateRenderError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Unexpected rendering error: {exc}"}

    return {"subject": subject, "html": html, "text": text}


@staff_member_required
def template_list(request: HttpRequest) -> HttpResponse:
    """Render the template list page with search and pagination.

    Supports HTMX partial requests: when ``HX-Request`` is present,
    returns only the ``_table.html`` partial.
    """
    qs = EmailTemplate.objects.annotate(
        campaign_count=Count("campaigns", distinct=True),
    ).all()

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))

    qs = qs.order_by("name")

    page_number = request.GET.get("page", 1)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)

    filter_params = {}
    for key in ("q",):
        val = request.GET.get(key, "").strip()
        if val:
            filter_params[key] = val
    filter_qs = urlencode(filter_params)

    context = {
        "nav_active": "templates",
        "templates": page_obj.object_list,
        "page_obj": page_obj,
        "current_q": request.GET.get("q", ""),
        "filter_qs": filter_qs,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "templates_mgr/_table.html", context)

    return render(request, "templates_mgr/list.html", context)


@staff_member_required
def template_create(request: HttpRequest) -> HttpResponse:
    """Handle template creation."""
    if request.method == "POST":
        form = EmailTemplateForm(request.POST)
        if form.is_valid():
            template = form.save()
            messages.success(
                request,
                _('Template "%(name)s" created.') % {"name": template.name},
            )
            return redirect("templates_mgr:detail", slug=template.slug)
    else:
        form = EmailTemplateForm()

    context = {
        "nav_active": "templates",
        "form": form,
        "is_edit": False,
    }
    return render(request, "templates_mgr/form.html", context)


@staff_member_required
def template_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Render template detail with live preview and campaign usage."""
    template = get_object_or_404(EmailTemplate, slug=slug)

    preview = _render_preview(template)

    campaigns_using = Campaign.objects.filter(
        template=template,
    ).select_related("email_type").order_by("-created_at")[:10]

    context = {
        "nav_active": "templates",
        "template": template,
        "preview": preview,
        "campaigns_using": campaigns_using,
    }
    return render(request, "templates_mgr/detail.html", context)


@staff_member_required
def template_edit(request: HttpRequest, slug: str) -> HttpResponse:
    """Handle template editing."""
    template = get_object_or_404(EmailTemplate, slug=slug)

    if request.method == "POST":
        form = EmailTemplateForm(request.POST, instance=template)
        if form.is_valid():
            updated = form.save()
            messages.success(
                request,
                _('Template "%(name)s" updated.') % {"name": updated.name},
            )
            return redirect("templates_mgr:detail", slug=updated.slug)
    else:
        form = EmailTemplateForm(instance=template)

    preview = _render_preview(template)

    context = {
        "nav_active": "templates",
        "form": form,
        "template": template,
        "preview": preview,
        "is_edit": True,
        "template_vars": [
            "subscriber.email", "subscriber.name", "subscriber.id",
            "subscriber.metadata.plan", "subscriber.health_score",
            "unsubscribe_url", "preferences_url",
            "app.name", "app.base_url",
        ],
    }
    return render(request, "templates_mgr/form.html", context)


@staff_member_required
def template_preview_htmx(request: HttpRequest, slug: str) -> HttpResponse:
    """HTMX endpoint: re-render preview from current saved template."""
    template = get_object_or_404(EmailTemplate, slug=slug)
    preview = _render_preview(template)
    return render(request, "templates_mgr/_preview.html", {"preview": preview})
