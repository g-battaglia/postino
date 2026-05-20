"""Subscriber forms for Postino.

Forms for CSV import with validation for required columns and file type,
and bulk action forms for tag, export, and suppress operations.
"""

from django import forms
from django.utils.translation import gettext_lazy as _


class SubscriberImportForm(forms.Form):
    """CSV file upload form for bulk subscriber import.

    Accepts a CSV file with at least an ``email`` column. Optional columns
    include ``name`` and ``tag``. A default tag can be specified to apply
    to all imported rows.
    """

    csv_file = forms.FileField(
        label=_("CSV file"),
        help_text=_("Upload a CSV file with at least an 'email' column."),
        widget=forms.ClearableFileInput(
            attrs={"accept": ".csv,text/csv"},
        ),
    )
    default_tag = forms.CharField(
        label=_("Default tag"),
        required=False,
        max_length=100,
        help_text=_("Optional tag to apply to all imported subscribers."),
        widget=forms.TextInput(
            attrs={"placeholder": _("e.g. newsletter")},
        ),
    )

    def clean_csv_file(self) -> object:
        uploaded = self.cleaned_data["csv_file"]
        if not uploaded.name.endswith(".csv"):
            raise forms.ValidationError(_("Please upload a CSV file (.csv extension)."))
        return uploaded


class BulkTagForm(forms.Form):
    """Form for bulk-tagging selected subscribers."""

    tag = forms.CharField(
        max_length=100,
        label=_("Tag name"),
        widget=forms.TextInput(attrs={"placeholder": _("e.g. vip")}),
    )
    subscriber_ids = forms.CharField(
        widget=forms.HiddenInput,
        required=False,
    )


class BulkSuppressForm(forms.Form):
    """Form for bulk-suppressing selected subscribers."""

    reason = forms.CharField(
        max_length=100,
        initial="bulk_suppress",
        label=_("Reason"),
    )
    subscriber_ids = forms.CharField(
        widget=forms.HiddenInput,
        required=False,
    )
