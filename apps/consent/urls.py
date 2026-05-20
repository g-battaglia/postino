"""URL configuration for the consent app.

Public unauthenticated endpoints:
- ``/unsubscribe/`` -- manual unsubscribe form (GET renders, POST processes)
- ``/unsubscribe/one-click/`` -- RFC 8058 one-click endpoint (POST only)
- ``/confirm/`` -- double opt-in email confirmation (GET only)
- ``/preferences/`` -- preference center (GET renders, POST updates)

Staff-only admin endpoints:
- ``/gdpr/<uuid>/export/`` -- download subscriber data as JSON
- ``/gdpr/<uuid>/delete/`` -- initiate GDPR Art. 17 data deletion
"""

from django.urls import path

from apps.consent import views

app_name = "consent"

urlpatterns = [
    path("unsubscribe/", views.unsubscribe_view, name="unsubscribe"),
    path("unsubscribe/one-click/", views.unsubscribe_one_click, name="unsubscribe_one_click"),
    path("confirm/", views.confirm_email_view, name="confirm_email"),
    path("preferences/", views.preferences_view, name="preferences"),
    path("gdpr/<uuid:subscriber_id>/export/", views.gdpr_data_export, name="gdpr_export"),
    path("gdpr/<uuid:subscriber_id>/delete/", views.gdpr_data_delete, name="gdpr_delete"),
]
