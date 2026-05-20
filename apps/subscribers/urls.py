"""Subscriber URL configuration."""

from django.urls import path

from apps.subscribers.views import (
    subscriber_bulk_export,
    subscriber_bulk_suppress,
    subscriber_bulk_tag,
    subscriber_detail,
    subscriber_import,
    subscriber_list,
)

app_name = "subscribers"

urlpatterns = [
    path("", subscriber_list, name="list"),
    path("import/", subscriber_import, name="import"),
    path("bulk/tag/", subscriber_bulk_tag, name="bulk_tag"),
    path("bulk/suppress/", subscriber_bulk_suppress, name="bulk_suppress"),
    path("bulk/export/", subscriber_bulk_export, name="bulk_export"),
    path("<uuid:subscriber_id>/", subscriber_detail, name="detail"),
]
