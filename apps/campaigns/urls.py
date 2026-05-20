"""Campaign and sequence URL configuration."""

from django.urls import path

from apps.campaigns.views import (
    campaign_create,
    campaign_detail,
    campaign_edit,
    campaign_list,
    sequence_create,
    sequence_detail,
    sequence_edit,
    sequence_list,
    sequence_step_create,
    sequence_step_delete,
)

app_name = "campaigns"

urlpatterns = [
    # Campaigns
    path("", campaign_list, name="list"),
    path("new/", campaign_create, name="create"),
    path("<int:campaign_id>/", campaign_detail, name="detail"),
    path("<int:campaign_id>/edit/", campaign_edit, name="edit"),

    # Sequences
    path("sequences/", sequence_list, name="sequence_list"),
    path("sequences/new/", sequence_create, name="sequence_create"),
    path("sequences/<int:sequence_id>/", sequence_detail, name="sequence_detail"),
    path("sequences/<int:sequence_id>/edit/", sequence_edit, name="sequence_edit"),
    path(
        "sequences/<int:sequence_id>/steps/new/",
        sequence_step_create,
        name="sequence_step_create",
    ),
    path(
        "sequences/<int:sequence_id>/steps/<int:step_id>/delete/",
        sequence_step_delete,
        name="sequence_step_delete",
    ),
]
