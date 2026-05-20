"""Campaign and sequence forms for Postino.

Forms for creating and editing campaigns and sequences with validation
and automatic timezone handling.
"""

import json

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Campaign, Sequence


class CampaignForm(forms.ModelForm):
    """Create/edit form for Campaign.

    Exposes audience_filter as separate UI fields (tags, status, health range)
    and merges them into the JSON audience_filter on save.
    """

    audience_tags = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": _("e.g. paid, beta, vip"),
            "class": "w-full",
        }),
        help_text=_("Comma-separated tag names. Leave empty for all."),
    )
    audience_status = forms.ChoiceField(
        required=False,
        choices=[
            ("active", _("Active only")),
            ("", _("All statuses")),
        ],
        initial="active",
    )
    health_min = forms.IntegerField(required=False, min_value=0, max_value=100)
    health_max = forms.IntegerField(required=False, min_value=0, max_value=100)

    class Meta:
        model = Campaign
        fields = [
            "name",
            "email_type",
            "template",
            "subject_line",
            "scheduled_at",
        ]
        widgets = {
            "scheduled_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            af = self.instance.audience_filter or {}
            self.fields["audience_tags"].initial = ", ".join(af.get("tags", []))
            self.fields["audience_status"].initial = af.get("status", "active")
            self.fields["health_min"].initial = af.get("health_above")
            self.fields["health_max"].initial = af.get("health_below")

    def clean_scheduled_at(self):
        value = self.cleaned_data.get("scheduled_at")
        if value and timezone.is_naive(value):
            return timezone.make_aware(value)
        return value

    def save(self, commit: bool = True) -> Campaign:
        instance = super().save(commit=False)

        audience_filter: dict = {}
        status = self.cleaned_data.get("audience_status")
        if status:
            audience_filter["status"] = status
        tags_raw = self.cleaned_data.get("audience_tags", "")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        if tags:
            audience_filter["tags"] = tags
        h_min = self.cleaned_data.get("health_min")
        h_max = self.cleaned_data.get("health_max")
        if h_min is not None:
            audience_filter["health_above"] = h_min
        if h_max is not None:
            audience_filter["health_below"] = h_max
        instance.audience_filter = audience_filter

        if instance.scheduled_at:
            instance.status = Campaign.Status.SCHEDULED
        else:
            instance.status = Campaign.Status.DRAFT

        if commit:
            instance.save()
        return instance


class SequenceForm(forms.ModelForm):
    """Create/edit form for Sequence."""

    trigger_config = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": '{}',
                "class": "font-mono text-[12.5px]",
            },
        ),
    )

    class Meta:
        model = Sequence
        fields = ["name", "slug", "description", "is_active", "trigger_type", "trigger_config"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance and self.instance.pk:
            self.initial["trigger_config"] = json.dumps(
                self.instance.trigger_config or {}, indent=2,
            )

    def clean_trigger_config(self) -> dict:
        raw = self.cleaned_data.get("trigger_config")
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(
                _("Invalid JSON: %(error)s") % {"error": str(exc)},
            ) from exc
        if not isinstance(parsed, dict):
            raise forms.ValidationError(
                _("Trigger config must be a JSON object, not a %(type)s.")
                % {"type": type(parsed).__name__},
            )
        return parsed

    def save(self, commit: bool = True) -> Sequence:
        instance = super().save(commit=False)
        if commit:
            instance.save()
        return instance
