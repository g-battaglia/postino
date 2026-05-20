"""Template manager URL configuration."""

from django.urls import path

from apps.templates_mgr.views import (
    template_create,
    template_detail,
    template_edit,
    template_list,
    template_preview_htmx,
)

app_name = "templates_mgr"

urlpatterns = [
    path("", template_list, name="list"),
    path("new/", template_create, name="create"),
    path("<slug:slug>/", template_detail, name="detail"),
    path("<slug:slug>/edit/", template_edit, name="edit"),
    path("<slug:slug>/preview/", template_preview_htmx, name="preview"),
]
