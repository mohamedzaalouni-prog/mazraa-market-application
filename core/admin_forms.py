from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm


class AdminEmailAuthenticationForm(AdminAuthenticationForm):
    error_messages = {
        "invalid_login": "Veuillez saisir un email ou identifiant et un mot de passe valides.",
        "inactive": "Ce compte est inactif.",
    }

    username = forms.CharField(
        label="Email ou identifiant",
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "placeholder": "Email ou identifiant",
            }
        ),
    )
