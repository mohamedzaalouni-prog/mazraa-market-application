from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from .models import Filiale, UserProfile
from .validators import clean_person_name, normalize_email, validate_not_disposable_email

cin_validator = RegexValidator(
    regex=r"^\d{8}$",
    message="Le CIN doit contenir exactement 8 chiffres.",
)


def get_filiale_queryset(*, active_only=False):
    queryset = Filiale.objects.order_by("sort_order", "name")
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset


def get_profile_for_instance(user):
    if not user or not user.pk:
        return None
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def has_other_active_staff(user):
    return User.objects.filter(is_staff=True, is_active=True).exclude(pk=user.pk).exists()


class PersonNameValidationMixin:
    def clean_first_name(self):
        return clean_person_name(self.cleaned_data["first_name"], self.fields["first_name"].label)

    def clean_last_name(self):
        return clean_person_name(self.cleaned_data["last_name"], self.fields["last_name"].label)


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Adresse email ou mot de passe incorrect.",
        "inactive": "Ce compte est inactif. Activez-le depuis l'email de confirmation.",
    }

    username = forms.EmailField(
        label="Adresse email",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Adresse email",
                "autocomplete": "email",
                "spellcheck": "false",
            }
        ),
    )
    password = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Mot de passe",
                "autocomplete": "current-password",
            }
        ),
    )
    remember_me = forms.BooleanField(required=False, initial=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update({"id": "id_login_username"})
        self.fields["password"].widget.attrs.update({"id": "id_login_password"})
        self.fields["remember_me"].widget.attrs.update({"id": "id_remember_me"})

    def clean_username(self):
        return normalize_email(self.cleaned_data["username"])

    def clean(self):
        email = normalize_email(self.data.get(self.add_prefix("username")))
        password = self.data.get(self.add_prefix("password"))

        if email and password:
            inactive_user = User.objects.filter(email__iexact=email, is_active=False).order_by("id").first()
            if inactive_user and inactive_user.check_password(password):
                raise forms.ValidationError(
                    self.error_messages["inactive"],
                    code="inactive",
                )

        self.cleaned_data = super(AuthenticationForm, self).clean()

        if email and password:
            self.user_cache = authenticate(self.request, username=email, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data


class SignUpForm(PersonNameValidationMixin, UserCreationForm):
    filiale = forms.ModelChoiceField(queryset=Filiale.objects.none(), empty_label="Choisir une filiale")
    cin = forms.CharField(min_length=8, max_length=8, label="CIN", validators=[cin_validator])
    first_name = forms.CharField(max_length=150, label="Nom")
    last_name = forms.CharField(max_length=150, label="Prenom")
    email = forms.EmailField(required=True, label="Email")

    class Meta:
        model = User
        fields = ("filiale", "cin", "first_name", "last_name", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._existing_user_for_cin = None
        self._pending_inactive_user = None
        self.fields["filiale"].queryset = get_filiale_queryset(active_only=True)
        self.fields["cin"].widget.attrs.update(
            {
                "placeholder": "CIN a 8 chiffres",
                "autocomplete": "username",
                "id": "id_signup_cin",
                "inputmode": "numeric",
                "maxlength": "8",
                "pattern": r"\d{8}",
            }
        )
        self.fields["first_name"].widget.attrs.update(
            {"placeholder": "Nom", "autocomplete": "given-name", "id": "id_signup_first_name"}
        )
        self.fields["last_name"].widget.attrs.update(
            {"placeholder": "Prenom", "autocomplete": "family-name", "id": "id_signup_last_name"}
        )
        self.fields["email"].widget.attrs.update(
            {"placeholder": "Email professionnel", "autocomplete": "email", "id": "id_signup_email"}
        )
        self.fields["password1"].widget.attrs.update(
            {"placeholder": "Mot de passe", "autocomplete": "new-password", "id": "id_signup_password1"}
        )
        self.fields["password2"].widget.attrs.update(
            {
                "placeholder": "Confirmation mot de passe",
                "autocomplete": "new-password",
                "id": "id_signup_password2",
            }
        )
        self.fields["password2"].label = "Confirmation mot de passe"
        self.fields.pop("username", None)

    def clean_cin(self):
        cin = self.cleaned_data["cin"].strip()
        existing_user = User.objects.filter(username=cin).first()
        self._existing_user_for_cin = existing_user
        if existing_user and existing_user.is_active:
            raise forms.ValidationError("Ce CIN est deja utilise.")
        return cin

    def clean_email(self):
        email = validate_not_disposable_email(self.cleaned_data["email"])
        existing_user = getattr(self, "_existing_user_for_cin", None)
        email_qs = User.objects.filter(email__iexact=email)
        if existing_user:
            email_qs = email_qs.exclude(pk=existing_user.pk)

        if email_qs.filter(is_active=True).exists():
            raise forms.ValidationError("Cette adresse email est deja utilisee.")

        pending_email_user = email_qs.filter(is_active=False).first()
        if pending_email_user and (not existing_user or pending_email_user.pk != existing_user.pk):
            raise forms.ValidationError(
                "Cette adresse email est deja associee a un compte en attente de verification."
            )

        if existing_user and not existing_user.is_active:
            self._pending_inactive_user = existing_user
        return email

    def save(self, commit=True):
        pending_user = getattr(self, "_pending_inactive_user", None)
        if pending_user:
            user = pending_user
            user.set_password(self.cleaned_data["password1"])
        else:
            user = super().save(commit=False)
        cin = self.cleaned_data["cin"].strip()
        user.username = cin
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.email = self.cleaned_data["email"].strip().lower()
        user.is_active = False
        if commit:
            user.save()
        return user


class FilialeSelectionForm(forms.Form):
    filiale = forms.ModelChoiceField(
        queryset=Filiale.objects.none(),
        empty_label=None,
        label="Filiale",
        widget=forms.RadioSelect,
    )

    def __init__(self, *args, filiales=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["filiale"].queryset = filiales or Filiale.objects.none()


class UserCreateForm(UserCreationForm):
    email = forms.EmailField(required=True, label="Email")
    cin = forms.CharField(min_length=8, max_length=8, label="CIN", validators=[cin_validator])
    filiale = forms.ModelChoiceField(queryset=Filiale.objects.none(), required=False, label="Filiale principale")
    accessible_filiales = forms.ModelMultipleChoiceField(
        queryset=Filiale.objects.none(),
        required=False,
        label="Filiales accessibles",
        help_text="Selectionne les filiales visibles pour ce compte.",
    )
    phone = forms.CharField(required=False, max_length=20, label="Telephone")
    position = forms.CharField(required=False, max_length=120, label="Poste")
    is_filiale_admin = forms.BooleanField(required=False, label="Admin filiale")
    is_superuser = forms.BooleanField(required=False, label="Superutilisateur")

    class Meta:
        model = User
        fields = (
            "cin",
            "first_name",
            "last_name",
            "email",
            "filiale",
            "accessible_filiales",
            "phone",
            "position",
            "is_filiale_admin",
            "is_staff",
            "is_superuser",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        filiales = get_filiale_queryset()
        self.fields["filiale"].queryset = filiales
        self.fields["accessible_filiales"].queryset = filiales
        self.fields["cin"].widget.attrs.update({"placeholder": "CIN a 8 chiffres", "inputmode": "numeric", "maxlength": "8"})
        self.fields["first_name"].widget.attrs.update({"placeholder": "Nom"})
        self.fields["last_name"].widget.attrs.update({"placeholder": "Prenom"})
        self.fields["email"].widget.attrs.update({"placeholder": "Email professionnel", "autocomplete": "email"})
        self.fields["phone"].widget.attrs.update({"placeholder": "Telephone"})
        self.fields["position"].widget.attrs.update({"placeholder": "Poste ou fonction"})
        self.fields["password1"].widget.attrs.update({"placeholder": "Mot de passe temporaire"})
        self.fields["password2"].widget.attrs.update({"placeholder": "Confirmer le mot de passe"})
        self.fields["password2"].label = "Confirmation du mot de passe"

    def clean_cin(self):
        cin = self.cleaned_data["cin"].strip()
        if User.objects.filter(username=cin).exists():
            raise forms.ValidationError("Ce CIN existe deja.")
        if UserProfile.objects.filter(cin=cin).exists():
            raise forms.ValidationError("Ce CIN existe deja dans un profil utilisateur.")
        return cin

    def clean_first_name(self):
        return clean_person_name(self.cleaned_data["first_name"], self.fields["first_name"].label)

    def clean_last_name(self):
        return clean_person_name(self.cleaned_data["last_name"], self.fields["last_name"].label)

    def clean_email(self):
        email = validate_not_disposable_email(self.cleaned_data["email"])
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Cette adresse email est deja utilisee.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        filiale = cleaned_data.get("filiale")
        accessible_filiales = cleaned_data.get("accessible_filiales")
        if filiale and accessible_filiales and filiale not in accessible_filiales:
            raise ValidationError("La filiale principale doit faire partie des filiales accessibles.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["cin"].strip()
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.email = self.cleaned_data["email"].strip().lower()
        user.is_superuser = self.cleaned_data["is_superuser"]
        if commit:
            user.save()
        return user

    def save_profile(self, user):
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"cin": user.username})
        profile.cin = self.cleaned_data["cin"].strip()
        profile.filiale = self.cleaned_data.get("filiale")
        profile.phone = self.cleaned_data.get("phone", "").strip()
        profile.position = self.cleaned_data.get("position", "").strip()
        profile.is_filiale_admin = self.cleaned_data.get("is_filiale_admin", False)
        profile.save()
        accessible_filiales = list(self.cleaned_data.get("accessible_filiales") or [])
        if profile.filiale and profile.filiale not in accessible_filiales:
            accessible_filiales.append(profile.filiale)
        profile.accessible_filiales.set(accessible_filiales)
        return profile


class UserUpdateForm(PersonNameValidationMixin, forms.ModelForm):
    cin = forms.CharField(min_length=8, max_length=8, label="CIN", validators=[cin_validator])
    filiale = forms.ModelChoiceField(queryset=Filiale.objects.none(), required=False, label="Filiale principale")
    accessible_filiales = forms.ModelMultipleChoiceField(
        queryset=Filiale.objects.none(),
        required=False,
        label="Filiales accessibles",
        help_text="Selectionne les filiales visibles pour ce compte.",
    )
    phone = forms.CharField(required=False, max_length=20, label="Telephone")
    position = forms.CharField(required=False, max_length=120, label="Poste")
    is_filiale_admin = forms.BooleanField(required=False, label="Admin filiale")
    new_password1 = forms.CharField(
        required=False,
        label="Nouveau mot de passe",
        strip=False,
        widget=forms.PasswordInput,
        help_text="Laissez vide pour conserver le mot de passe actuel.",
    )
    new_password2 = forms.CharField(
        required=False,
        label="Confirmation du nouveau mot de passe",
        strip=False,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = (
            "cin",
            "first_name",
            "last_name",
            "email",
            "filiale",
            "accessible_filiales",
            "phone",
            "position",
            "is_filiale_admin",
            "is_staff",
            "is_superuser",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        filiales = get_filiale_queryset()
        self.fields["filiale"].queryset = filiales
        self.fields["accessible_filiales"].queryset = filiales
        self.fields["cin"].widget.attrs.update({"placeholder": "CIN a 8 chiffres", "inputmode": "numeric", "maxlength": "8"})
        self.fields["first_name"].widget.attrs.update({"placeholder": "Nom"})
        self.fields["last_name"].widget.attrs.update({"placeholder": "Prenom"})
        self.fields["email"].widget.attrs.update({"placeholder": "Email professionnel", "autocomplete": "email"})
        self.fields["phone"].widget.attrs.update({"placeholder": "Telephone"})
        self.fields["position"].widget.attrs.update({"placeholder": "Poste ou fonction"})
        self.fields["new_password1"].widget.attrs.update({"placeholder": "Nouveau mot de passe"})
        self.fields["new_password2"].widget.attrs.update({"placeholder": "Confirmer le nouveau mot de passe"})
        if self.instance.pk:
            self.fields["cin"].initial = self.instance.username
            profile = get_profile_for_instance(self.instance)
            if profile:
                self.fields["phone"].initial = profile.phone
                self.fields["position"].initial = profile.position
                self.fields["is_filiale_admin"].initial = profile.is_filiale_admin
                self.fields["filiale"].initial = profile.filiale
                self.fields["accessible_filiales"].initial = profile.accessible_filiales.all()

    def clean_cin(self):
        cin = self.cleaned_data["cin"].strip()
        qs = User.objects.filter(username=cin).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Ce CIN existe deja.")
        profile = get_profile_for_instance(self.instance)
        profile_qs = UserProfile.objects.filter(cin=cin)
        if profile:
            profile_qs = profile_qs.exclude(pk=profile.pk)
        if profile_qs.exists():
            raise forms.ValidationError("Ce CIN existe deja dans un profil utilisateur.")
        return cin

    def clean_email(self):
        email = validate_not_disposable_email(self.cleaned_data["email"])
        qs = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Cette adresse email est deja utilisee.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        filiale = cleaned_data.get("filiale")
        accessible_filiales = cleaned_data.get("accessible_filiales")
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")

        if filiale and accessible_filiales and filiale not in accessible_filiales:
            raise ValidationError("La filiale principale doit faire partie des filiales accessibles.")

        if password1 or password2:
            if password1 != password2:
                raise ValidationError("Les mots de passe ne correspondent pas.")
            if password1:
                password_validation.validate_password(password1, self.instance)

        if self.instance.is_staff and (
            not cleaned_data.get("is_staff", False) or not cleaned_data.get("is_active", False)
        ) and not has_other_active_staff(self.instance):
            raise ValidationError("Au moins un compte staff actif doit rester disponible.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["cin"].strip()
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.email = self.cleaned_data["email"].strip().lower()
        if commit:
            user.save()
            new_password = self.cleaned_data.get("new_password1")
            if new_password:
                user.set_password(new_password)
                user.save(update_fields=["password"])
        return user

    def save_profile(self, user):
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={"cin": user.username})
        profile.cin = self.cleaned_data["cin"].strip()
        profile.filiale = self.cleaned_data.get("filiale")
        profile.phone = self.cleaned_data.get("phone", "").strip()
        profile.position = self.cleaned_data.get("position", "").strip()
        profile.is_filiale_admin = self.cleaned_data.get("is_filiale_admin", False)
        profile.save()
        accessible_filiales = list(self.cleaned_data.get("accessible_filiales") or [])
        if profile.filiale and profile.filiale not in accessible_filiales:
            accessible_filiales.append(profile.filiale)
        profile.accessible_filiales.set(accessible_filiales)
        return profile


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="Adresse email",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Email professionnel",
                "autocomplete": "email",
            }
        ),
    )

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])


class ResendActivationForm(forms.Form):
    email = forms.EmailField(
        label="Adresse email",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Adresse email utilisee a l'inscription",
                "autocomplete": "email",
            }
        ),
    )

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])


class PasswordResetVerifyForm(forms.Form):
    email = forms.EmailField(
        label="Adresse email",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "Adresse email du compte",
                "autocomplete": "email",
            }
        ),
    )
    code = forms.CharField(
        label="Code de verification",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Code a 6 chiffres",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
            }
        ),
    )
    new_password1 = forms.CharField(
        label="Nouveau mot de passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Nouveau mot de passe",
                "autocomplete": "new-password",
            }
        ),
    )
    new_password2 = forms.CharField(
        label="Confirmation du mot de passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Confirmer le mot de passe",
                "autocomplete": "new-password",
            }
        ),
    )

    def __init__(self, *args, user=None, initial_email="", **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if initial_email and not self.is_bound:
            self.fields["email"].initial = initial_email

    def clean_email(self):
        return normalize_email(self.cleaned_data["email"])

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise forms.ValidationError("Le code doit contenir 6 chiffres.")
        return code

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")

        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Les mots de passe ne correspondent pas.")

        if password1 and self.user:
            password_validation.validate_password(password1, self.user)

        return cleaned_data


class AccountPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label="Mot de passe actuel",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Mot de passe actuel",
                "autocomplete": "current-password",
            }
        ),
    )
    new_password1 = forms.CharField(
        label="Nouveau mot de passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Nouveau mot de passe",
                "autocomplete": "new-password",
            }
        ),
    )
    new_password2 = forms.CharField(
        label="Confirmation du nouveau mot de passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Confirmer le nouveau mot de passe",
                "autocomplete": "new-password",
            }
        ),
    )


class FilialeForm(forms.ModelForm):
    class Meta:
        model = Filiale
        fields = ("name", "code", "description", "sort_order", "is_active")
        labels = {
            "name": "Nom",
            "code": "Code",
            "description": "Description",
            "sort_order": "Ordre d'affichage",
            "is_active": "Filiale active",
        }
        help_texts = {
            "code": "Identifiant unique utilise dans la plateforme, par exemple mazraa-market.",
            "description": "Texte affiche dans les interfaces de selection et d'administration.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "Description de la filiale"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "Nom de la filiale"})
        self.fields["code"].widget.attrs.update({"placeholder": "code-filiale"})
        self.fields["sort_order"].widget.attrs.update({"min": "0"})


class MLForm(forms.Form):
    input_data = forms.CharField(
        label="Données d'entrée pour ML",
        widget=forms.Textarea(
            attrs={
                "placeholder": "Entrez les données pour l'analyse ML",
                "rows": 4,
            }
        ),
        help_text="Entrez les données que vous souhaitez analyser avec le modèle de ML.",
    )
