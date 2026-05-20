"""Forms for the public unsubscribe and preference center flows.

The unsubscribe form presents three options to the subscriber:
per-type withdrawal, global unsubscribe, and GDPR data deletion.
The per-type option is only shown when an email type slug is known,
and its label includes the human-readable email type name.

The preference form allows per-type consent grants/withdrawals via
checkboxes, plus global unsubscribe and data deletion actions.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _


class UnsubscribeForm(forms.Form):
    """CSRF-protected form for the manual (visible) unsubscribe page."""

    CHOICES_PER_TYPE = "per_type"
    CHOICES_GLOBAL = "global"
    CHOICES_DELETION = "deletion"

    action = forms.ChoiceField(
        choices=[
            (CHOICES_PER_TYPE, _("Stop receiving this type of email")),
            (CHOICES_GLOBAL, _("Unsubscribe from all emails")),
            (CHOICES_DELETION, _("Delete all my data")),
        ],
        widget=forms.RadioSelect,
        initial=CHOICES_PER_TYPE,
    )

    token = forms.CharField(widget=forms.HiddenInput, required=True)
    email_type_slug = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(
        self,
        *args,
        has_email_type: bool = True,
        email_type_name: str = "",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if has_email_type and email_type_name:
            from django.utils.text import format_lazy

            per_type_label = format_lazy(
                _("Stop receiving {name} emails"),
                name=email_type_name,
            )
            self.fields["action"].choices = [
                (self.CHOICES_PER_TYPE, per_type_label),
                (self.CHOICES_GLOBAL, _("Unsubscribe from all emails")),
                (self.CHOICES_DELETION, _("Delete all my data")),
            ]
        elif has_email_type:
            self.fields["action"].choices = [
                (self.CHOICES_PER_TYPE, _("Stop receiving this type of email")),
                (self.CHOICES_GLOBAL, _("Unsubscribe from all emails")),
                (self.CHOICES_DELETION, _("Delete all my data")),
            ]
        else:
            self.fields["action"].choices = [
                (self.CHOICES_GLOBAL, _("Unsubscribe from all emails")),
                (self.CHOICES_DELETION, _("Delete all my data")),
            ]
            self.fields["action"].initial = self.CHOICES_GLOBAL
            self.fields["email_type_slug"].widget = forms.HiddenInput(
                attrs={"disabled": True}
            )


class PreferenceForm(forms.Form):
    """CSRF-protected form for the preference center page.

    Dynamic checkboxes are generated per active email type. The hidden
    ``token`` field preserves the subscriber's HMAC token across POST.
    The ``global_action`` field allows explicit global unsubscribe or
    data deletion.
    """

    token = forms.CharField(widget=forms.HiddenInput, required=True)
    global_action = forms.ChoiceField(
        choices=[("", "---------")],
        required=False,
        widget=forms.Select,
    )

    GLOBAL_ACTION_UNSUBSCRIBE = "global_unsubscribe"
    GLOBAL_ACTION_DELETION = "deletion"

    def __init__(self, *args, token: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["token"].initial = token
        self.fields["global_action"].choices = [
            ("", "---------"),
            (self.GLOBAL_ACTION_UNSUBSCRIBE, _("Unsubscribe from all emails")),
            (self.GLOBAL_ACTION_DELETION, _("Delete all my data")),
        ]
