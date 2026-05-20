"""Sequence URL configuration — mounted at /sequences/ in root urlconf."""

from django.urls import path

from apps.campaigns.views import (
    sequence_create,
    sequence_detail,
    sequence_edit,
    sequence_list,
    sequence_step_create,
    sequence_step_delete,
)

app_name = "sequences"

urlpatterns = [
    path("", sequence_list, name="list"),
    path("new/", sequence_create, name="create"),
    path("<int:sequence_id>/", sequence_detail, name="detail"),
    path("<int:sequence_id>/edit/", sequence_edit, name="edit"),
    path(
        "<int:sequence_id>/steps/new/",
        sequence_step_create,
        name="step_create",
    ),
    path(
        "<int:sequence_id>/steps/<int:step_id>/delete/",
        sequence_step_delete,
        name="step_delete",
    ),
]
