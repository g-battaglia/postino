"""Template manager forms for Postino.

Provides the EmailTemplateForm for creating and editing email templates.
Validates slug uniqueness via the model. Template syntax errors are
caught at preview time on the detail page, not during form validation.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import EmailTemplate


class EmailTemplateForm(forms.ModelForm):
    """Create/edit form for EmailTemplate.

    Validates slug uniqueness. When editing an existing template, the
    slug uniqueness check excludes the current instance.
    """

    class Meta:
        model = EmailTemplate
        fields = ["name", "slug", "subject_default", "html_body", "text_body"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": _("e.g. Welcome Email"),
                },
            ),
            "slug": forms.TextInput(
                attrs={
                    "placeholder": _("e.g. welcome-email"),
                },
            ),
            "subject_default": forms.TextInput(
                attrs={
                    "placeholder": _("e.g. Welcome to {{ app_name }}, {{ subscriber_name }}!"),
                },
            ),
            "html_body": forms.Textarea(
                attrs={
                    "rows": 12,
                    "placeholder": _(
                        "<h1>Hello {{ subscriber_name }}</h1>"
                        "\n<p>Your content here.</p>"
                    ),
                    "class": "font-mono text-[12.5px]",
                },
            ),
            "text_body": forms.Textarea(
                attrs={
                    "rows": 6,
                    "placeholder": _("Hello {{ subscriber_name }}\n\nYour content here."),
                    "class": "font-mono text-[12.5px]",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["slug"].help_text = _(
                "Changing the slug updates this template's URL and CLI identifier. "
                "Campaigns linked by reference are not affected."
            )
