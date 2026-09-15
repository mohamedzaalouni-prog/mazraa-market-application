import logging
import requests
import secrets
import socket
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from django.contrib import messages
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django import forms
from django.db.models import Q
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .forms import (
    FilialeSelectionForm,
    FilialeForm,
    AccountPasswordChangeForm,
    LoginForm,
    MLForm,
    PasswordResetRequestForm,
    PasswordResetVerifyForm,
    ResendActivationForm,
    SignUpForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import Filiale, PasswordResetCode, UserProfile
from .validators import normalize_email

from prediction.views import load_forecast_data


DEFAULT_FILIALES = [
    {
        "name": "MAZRAA MARKET",
        "code": "mazraa-market",
        "description": "Filiale principale pour le suivi des ventes, achats et indicateurs retail.",
        "sort_order": 1,
        "is_active": True,
    },
    {
        "name": "GIPA",
        "code": "gipa",
        "description": "Filiale orientee distribution et logistique operationnelle.",
        "sort_order": 2,
        "is_active": False,
    },
    {
        "name": "JADIDA",
        "code": "jadida",
        "description": "Filiale disponible dans la plateforme pour le suivi et les dashboards.",
        "sort_order": 3,
        "is_active": False,
    },
]

logger = logging.getLogger(__name__)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_SECONDS = 900
PASSWORD_RESET_EMAIL_SESSION_KEY = "password_reset_email"
ACTIVATION_RESEND_COOLDOWN_SECONDS = 60


def ensure_default_filiales():
    if Filiale.objects.exists():
        return list(Filiale.objects.order_by("sort_order", "name"))

    return [Filiale.objects.create(**item) for item in DEFAULT_FILIALES]


def get_user_profile(user):
    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={"cin": user.username},
    )
    return profile


def get_accessible_filiales(user):
    ensure_default_filiales()
    if user.is_staff:
        return Filiale.objects.filter(is_active=True).order_by("sort_order", "name")

    profile = get_user_profile(user)
    filiales = profile.accessible_filiales.filter(
        is_active=True,
    ).order_by("sort_order", "name")
    if not filiales.exists() and profile.filiale:
        if profile.filiale.is_active:
            profile.accessible_filiales.add(profile.filiale)
        filiales = profile.accessible_filiales.filter(
            is_active=True,
        ).order_by("sort_order", "name")
    return filiales


def get_filiale_catalog():
    ensure_default_filiales()
    return Filiale.objects.order_by("sort_order", "name")


def filiale_has_data(filiale):
    if not filiale:
        return False
    return filiale.code == "mazraa-market"


def is_secure_powerbi_embed_url(url):
    return "reportembed" in (url or "").lower()


def get_workspace_links():
    return [
        {
            "title": "Tableaux de bord",
            "label": "Espace tableaux de bord",
            "url_name": "dashboard",
            "code": "01",
            "description": "Acceder au bloc Accueil, Achats et Ventes de la filiale.",
        },
        {
            "title": "Prévision",
            "label": "Espace previsionnel",
            "url_name": "forecast",
            "code": "02",
            "description": "Acceder a la recherche, aux horizons et aux simulations previsionnelles.",
        },
    ]


def get_admin_links():
    return [
        {
            "title": "Gestion d'utilisateurs",
            "url_name": "users_list",
            "code": "01",
            "description": "Creer, modifier et suivre les comptes, les roles et les acces filiales.",
        },
        {
            "title": "Gestion des filiales",
            "url_name": "filiales_list",
            "code": "02",
            "description": "Configurer les filiales, leurs statuts et les informations d'affichage.",
        },
    ]


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def get_login_attempt_cache_key(identifier, ip_address):
    return f"login-attempts:{identifier}:{ip_address}"


def clear_login_attempts(identifier, ip_address):
    cache.delete(get_login_attempt_cache_key(identifier, ip_address))


def build_password_reset_code():
    return f"{secrets.randbelow(900000) + 100000}"


def get_activation_resend_cache_key(user_id):
    return f"activation-resend:{user_id}"


def get_lan_site_base_url(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            lan_ip = sock.getsockname()[0]
    except OSError:
        return ""

    if not lan_ip or lan_ip.startswith("127."):
        return ""
    return f"http://{lan_ip}:{port}"


def get_site_base_url(request=None):
    configured_base_url = settings.SITE_BASE_URL.rstrip("/")
    parsed_base_url = urlparse(configured_base_url)
    configured_host = (parsed_base_url.hostname or "").lower()

    if settings.DEBUG and configured_host in {"127.0.0.1", "localhost"}:
        if request:
            request_host = request.get_host()
            request_hostname = request_host.split(":", 1)[0].lower()
            if request_hostname not in {"127.0.0.1", "localhost"}:
                return f"{request.scheme}://{request_host}".rstrip("/")

        return get_lan_site_base_url(parsed_base_url.port or 8000) or configured_base_url

    return configured_base_url


def build_absolute_url(path, request=None):
    base_url = get_site_base_url(request)
    return f"{base_url}{path}"


def mask_email_address(email):
    if not email or "@" not in email:
        return email
    local_part, domain = email.split("@", 1)
    if len(local_part) <= 2:
        masked_local = local_part[0] + "*"
    else:
        masked_local = f"{local_part[:2]}{'*' * (len(local_part) - 2)}"
    return f"{masked_local}@{domain}"


def send_account_email(*, subject, text_template, html_template, recipient_list, context):
    text_body = render_to_string(text_template, context).strip()
    html_body = render_to_string(html_template, context)
    email_message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipient_list,
    )
    email_message.attach_alternative(html_body, "text/html")
    email_message.send(fail_silently=False)


def send_activation_email(user, request=None):
    activation_path = reverse(
        "activate_account",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": default_token_generator.make_token(user),
        },
    )
    activation_url = build_absolute_url(activation_path, request=request)
    context = {
        "user": user,
        "activation_url": activation_url,
        "site_base_url": get_site_base_url(request),
    }
    send_account_email(
        subject="Activez votre compte Portail PGH",
        text_template="registration/emails/activation_email.txt",
        html_template="registration/emails/activation_email.html",
        recipient_list=[user.email],
        context=context,
    )


def send_password_reset_code_email(user, raw_code, request=None):
    context = {
        "user": user,
        "code": raw_code,
        "expires_minutes": PasswordResetCode.CODE_VALIDITY_MINUTES,
        "site_base_url": get_site_base_url(request),
    }
    send_account_email(
        subject="Code de reinitialisation Portail PGH",
        text_template="registration/emails/password_reset_code_email.txt",
        html_template="registration/emails/password_reset_code_email.html",
        recipient_list=[user.email],
        context=context,
    )


def is_staff_user(user):
    return user.is_authenticated and user.is_staff


def admin_user_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied("Vous n'etes pas autorise a acceder a cette zone d'administration.")
        return view_func(request, *args, **kwargs)

    return _wrapped_view


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("admin_dashboard")
        return redirect("filiale_select")

    form = LoginForm(request, data=request.POST or None)
    if "username" in form.fields:
        form.fields["username"].label = "Email"
        form.fields["username"].widget = forms.EmailInput(
            attrs={
                "placeholder": "Votre email",
                "autocomplete": "new-password",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        )
    if "password" in form.fields:
        form.fields["password"].label = "Mot de passe"
        form.fields["password"].widget.attrs.update(
            {
                "placeholder": "Votre mot de passe",
                "autocomplete": "new-password",
            }
        )
    ip_address = get_client_ip(request)
    identifier = normalize_email(request.POST.get("username") or request.GET.get("username") or "anonymous") or "anonymous"
    cache_key = get_login_attempt_cache_key(identifier, ip_address)
    failed_attempts = cache.get(cache_key, 0)

    if failed_attempts >= LOGIN_MAX_ATTEMPTS:
        form.add_error(None, "Trop de tentatives de connexion. Reessayez dans 15 minutes.")

    if request.method == "POST":
        posted_login = normalize_email(request.POST.get("username") or "")
        inactive_user = None
        if posted_login:
            inactive_user = User.objects.filter(email__iexact=posted_login, is_active=False).first()
        if inactive_user:
            form.add_error(
                None,
                "Ce compte existe, mais il n'est pas encore active. Ouvrez le lien d'activation envoye par email.",
            )
            return render(request, "registration/login.html", {"form": form, "next": request.GET.get("next", "")})

    if request.method == "POST" and form.is_valid():
        auth_login(request, form.get_user())
        clear_login_attempts(form.cleaned_data.get("username", identifier), ip_address)
        if not form.cleaned_data.get("remember_me"):
            request.session.set_expiry(0)
        request.session["just_logged_in"] = True
        redirect_to = request.POST.get("next") or request.GET.get("next")
        if not redirect_to:
            redirect_to = "admin_dashboard" if form.get_user().is_staff else "filiale_select"
        return redirect(redirect_to)
    elif request.method == "POST" and failed_attempts < LOGIN_MAX_ATTEMPTS:
        cache.set(cache_key, failed_attempts + 1, LOGIN_LOCKOUT_SECONDS)

    return render(request, "registration/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_http_methods(["GET", "POST"])
@never_cache
def signup(request):
    if request.user.is_authenticated:
        auth_logout(request)

    ensure_default_filiales()
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            filiale = form.cleaned_data["filiale"]
            profile, _ = UserProfile.objects.get_or_create(
                user=user,
                defaults={"cin": form.cleaned_data["cin"].strip()},
            )
            profile.cin = form.cleaned_data["cin"].strip()
            profile.filiale = filiale
            profile.is_filiale_admin = False
            profile.save()
            profile.accessible_filiales.set([filiale])

        request.session["pending_activation_email"] = user.email
        try:
            send_activation_email(user, request=request)
            cache.set(get_activation_resend_cache_key(user.pk), True, ACTIVATION_RESEND_COOLDOWN_SECONDS)
            messages.success(request, "Votre compte a ete cree. Consultez votre email pour l'activer.")
        except Exception:
            logger.exception("Unable to send activation email for user %s", user.pk)
            messages.error(
                request,
                "Le compte a ete cree, mais l'email d'activation n'a pas pu etre envoye. Vous pouvez renvoyer le lien depuis la page suivante.",
            )
        return redirect("signup_pending")

    return render(
        request,
        "registration/signup.html",
        {
            "form": form,
            "email_backend_console": settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend",
        },
    )


@require_http_methods(["GET"])
def signup_pending_view(request):
    pending_email = request.session.get("pending_activation_email", "")
    resend_form = ResendActivationForm(initial={"email": pending_email})
    context = {
        "masked_email": mask_email_address(pending_email),
        "resend_form": resend_form,
        "email_backend_console": settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend",
    }
    return render(request, "registration/signup_pending.html", context)


@require_http_methods(["POST"])
def resend_activation_view(request):
    form = ResendActivationForm(request.POST)
    if form.is_valid():
        email = form.cleaned_data["email"]
        request.session["pending_activation_email"] = email
        user = User.objects.filter(email__iexact=email, is_active=False).order_by("id").first()
        if user and not cache.get(get_activation_resend_cache_key(user.pk)):
            try:
                send_activation_email(user, request=request)
                cache.set(get_activation_resend_cache_key(user.pk), True, ACTIVATION_RESEND_COOLDOWN_SECONDS)
            except Exception:
                logger.exception("Unable to resend activation email for user %s", user.pk)
        messages.success(
            request,
            "Si cette adresse attend encore une activation, un nouveau lien a ete envoye.",
        )
    else:
        messages.error(request, "Saisissez une adresse email valide.")
    return redirect("signup_pending")


@require_http_methods(["GET"])
@never_cache
def activate_account_view(request, uidb64, token):
    user = None
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.filter(pk=user_id).first()
    except (TypeError, ValueError, OverflowError):
        user = None

    if request.user.is_authenticated and (not user or request.user.pk != user.pk):
        auth_logout(request)

    if user and user.is_active:
        messages.success(request, "Ce compte est deja actif. Vous pouvez vous connecter.")
        return render(request, "registration/activation_success.html")

    if user and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save(update_fields=["is_active"])
        if request.session.get("pending_activation_email", "").lower() == user.email.lower():
            request.session.pop("pending_activation_email", None)
        messages.success(request, "Votre compte a ete active avec succes. Vous pouvez maintenant vous connecter.")
        return render(request, "registration/activation_success.html")

    messages.error(request, "Le lien d'activation est invalide ou a expire.")
    return render(request, "registration/activation_failed.html", status=400)


@require_http_methods(["GET", "POST"])
def password_reset_request_view(request):
    form = PasswordResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        request.session[PASSWORD_RESET_EMAIL_SESSION_KEY] = email
        user = User.objects.filter(Q(email__iexact=email), is_active=True).order_by("id").first()
        if user:
            active_code = (
                PasswordResetCode.objects.filter(
                    user=user,
                    is_used=False,
                    is_verified=False,
                    expires_at__gt=timezone.now(),
                )
                .order_by("-created_at")
                .first()
            )
            should_send_new_code = (
                not active_code
                or active_code.created_at <= timezone.now() - timedelta(seconds=PasswordResetCode.RESEND_COOLDOWN_SECONDS)
            )
            if should_send_new_code:
                PasswordResetCode.objects.filter(user=user, is_used=False).update(is_used=True)
                raw_code = build_password_reset_code()
                reset_code = PasswordResetCode.build_for_user(user=user, raw_code=raw_code)
                reset_code.save()
                try:
                    send_password_reset_code_email(user, raw_code, request=request)
                except Exception:
                    logger.exception("Unable to send password reset email for user %s", user.pk)
                    reset_code.is_used = True
                    reset_code.save(update_fields=["is_used"])

        messages.success(
            request,
            "Si cette adresse email existe, un code de reinitialisation a ete envoye.",
        )
        return redirect("password_reset_verify")

    return render(
        request,
        "registration/password_reset_form.html",
        {
            "form": form,
            "email_backend_console": settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend",
        },
    )


@require_http_methods(["GET", "POST"])
def password_reset_verify_view(request):
    initial_email = request.session.get(PASSWORD_RESET_EMAIL_SESSION_KEY, "")
    user = None
    if request.method == "POST":
        posted_email = (request.POST.get("email") or "").strip().lower()
        user = User.objects.filter(email__iexact=posted_email, is_active=True).order_by("id").first()

    form = PasswordResetVerifyForm(request.POST or None, user=user, initial_email=initial_email)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        request.session[PASSWORD_RESET_EMAIL_SESSION_KEY] = email
        user = User.objects.filter(email__iexact=email, is_active=True).order_by("id").first()
        reset_code = None
        if user:
            reset_code = (
                PasswordResetCode.objects.filter(
                    user=user,
                    is_used=False,
                    is_verified=False,
                )
                .order_by("-created_at")
                .first()
            )

        if not reset_code:
            form.add_error("code", "Le code est invalide ou a expire.")
        elif reset_code.is_expired():
            reset_code.is_used = True
            reset_code.save(update_fields=["is_used"])
            form.add_error("code", "Le code est invalide ou a expire.")
        elif reset_code.attempts >= PasswordResetCode.MAX_ATTEMPTS:
            reset_code.is_used = True
            reset_code.save(update_fields=["is_used"])
            form.add_error("code", "Le code est invalide ou a expire.")
        elif not reset_code.matches(form.cleaned_data["code"]):
            reset_code.attempts += 1
            if reset_code.attempts >= PasswordResetCode.MAX_ATTEMPTS:
                reset_code.is_used = True
                reset_code.save(update_fields=["attempts", "is_used"])
            else:
                reset_code.save(update_fields=["attempts"])
            form.add_error("code", "Le code est invalide ou a expire.")
        else:
            with transaction.atomic():
                user.set_password(form.cleaned_data["new_password1"])
                user.save(update_fields=["password"])
                reset_code.is_verified = True
                reset_code.is_used = True
                reset_code.save(update_fields=["is_verified", "is_used"])
                PasswordResetCode.objects.filter(user=user, is_used=False).exclude(id=reset_code.id).update(is_used=True)
            request.session.pop(PASSWORD_RESET_EMAIL_SESSION_KEY, None)
            messages.success(
                request,
                "Votre mot de passe a ete mis a jour. Connectez-vous avec le nouveau mot de passe.",
            )
            return redirect("login")

    context = {
        "form": form,
        "masked_email": mask_email_address(initial_email),
        "email_backend_console": settings.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend",
    }
    return render(request, "registration/password_reset_verify.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def filiale_select(request):
    filiales = get_accessible_filiales(request.user)
    future_filiales = get_filiale_catalog().filter(is_active=False)
    if not filiales.exists():
        messages.error(request, "Aucune filiale n'est associee a ce compte.")
        auth_logout(request)
        return redirect("login")

    form = FilialeSelectionForm(request.POST or None, filiales=filiales)
    if request.method == "POST" and form.is_valid():
        request.session["selected_filiale_id"] = form.cleaned_data["filiale"].id
        return redirect("workspace_home")

    return render(
        request,
        "filiales/select.html",
        {
            "page_name": "filiales",
            "form": form,
            "filiales": filiales,
            "future_filiales": future_filiales,
            "total_filiales": filiales.count() + future_filiales.count(),
            "just_logged_in": bool(request.session.pop("just_logged_in", False)),
        },
    )


def get_selected_filiale(request):
    filiale_id = request.session.get("selected_filiale_id")
    if not filiale_id:
        return None
    allowed = get_accessible_filiales(request.user)
    return allowed.filter(id=filiale_id).first()


@login_required
def home(request):
    return redirect("workspace_home")


@login_required
def workspace_home(request):
    filiale = get_selected_filiale(request)
    if not filiale:
        return redirect("filiale_select")
    has_data = filiale_has_data(filiale)

    context = {
        "page_name": "workspace",
        "filiale": filiale,
        "filiale_has_data": has_data,
        "hero_title": f"Espace {filiale.name}",
        "hero_text": (
            "Choisis l'espace de travail a ouvrir. Le dashboard contient Accueil, Achats et Ventes, "
            "tandis que la Prévision reste dans un espace separe pour la recherche et les projections."
        ),
        "hero_meta": [
            "Choix du module",
            "Navigation filiale",
            "Parcours clair",
        ],
        "workspace_links": get_workspace_links(),
        "workspace_steps": [
            "Selection de la filiale",
            "Ouverture de l'espace Mazraa Market",
            "Choix entre Dashboard et Prévision",
            "Navigation metier dans l'espace choisi",
        ],
    }
    return render(request, "workspace/home.html", context)


@login_required
def dashboard_view(request):
    filiale = get_selected_filiale(request)
    if not filiale:
        return redirect("filiale_select")
    has_data = filiale_has_data(filiale)

    powerbi_embed_url = settings.POWERBI_DASHBOARD_EMBED_URL
    configured_powerbi_open_url = settings.POWERBI_DASHBOARD_OPEN_URL
    is_secure_powerbi = is_secure_powerbi_embed_url(powerbi_embed_url)
    powerbi_open_url = configured_powerbi_open_url or (powerbi_embed_url if is_secure_powerbi else "")

    context = {
        "page_name": "dashboard",
        "filiale": filiale,
        "filiale_has_data": has_data,
        "hero_title": f"Accueil - {filiale.name}",
        "hero_text": (
            "Page principale du dashboard filiale pour le suivi executif des KPI, "
            "avec une zone dediee au titre dynamique, aux filtres et a l'embed Power BI."
        ),
        "hero_meta": [
            "Tableau de bord exécutif",
            "Mazraa active",
            "Titre et filtre date",
        ],
        "hero_actions": [
            {"label": "Aller vers achats", "url_name": "purchases", "kind": "primary"},
            {"label": "Retour espace Mazraa", "url_name": "workspace_home", "kind": "secondary"},
        ],
        "powerbi_embed_url": powerbi_embed_url,
        "powerbi_open_url": powerbi_open_url,
        "is_secure_powerbi": is_secure_powerbi,
        "summary_cards": [
            {
                "label": "Chiffre d'affaires",
                "value": "12.4 M TND",
                "trend": "+8.2%",
                "tone": "good",
                "detail": "Comparaison mensuelle et lecture executive du chiffre d'affaires",
            },
            {
                "label": "Marge brute",
                "value": "18.7%",
                "trend": "+1.3 pt",
                "tone": "neutral",
                "detail": "Marge consolidee a afficher dans le dashboard principal",
            },
            {
                "label": "Taux service",
                "value": "96.2%",
                "trend": "-0.6 pt",
                "tone": "good",
                "detail": "Bloc prete pour les alertes logistiques et operationnelles",
            },
            {
                "label": "Vue active",
                "value": "PBI",
                "trend": "Integration prete",
                "tone": "alert",
                "detail": "Zone reservee pour le rapport principal et les filtres metiers",
            },
        ],
        "dashboard_filters": [
            "Periode",
            "Region",
            "Famille produit",
            "Canal",
            "Centre de cout",
        ],
        "dashboard_controls": [
            {"title": "Mise a jour du titre", "text": "Remplacer le titre statique par le titre dynamique du rapport ou du visuel actif."},
            {"title": "Filtre date", "text": "Connecter un selecteur de date ou une timeline pour piloter l'analyse."},
            {"title": "KPI executifs", "text": "Afficher les cartes CA, marge, taux de service et alertes prioritaires."},
        ],
        "governance_cards": [
            {
                "title": "Data staging",
                "text": "Le dashboard final sera alimente depuis la zone de staging puis le data warehouse SQL Server.",
                "value": "SSIS pret",
            },
            {
                "title": "Visualisation",
                "text": "La page est concue pour recevoir un embed Power BI sans refaire le layout.",
                "value": "Power BI",
            },
            {
                "title": "Couche decisionnelle",
                "text": "Les CTA gardent un lien propre vers achats, ventes et prevision, sans surcharger la page.",
                "value": "Structure dediee",
            },
        ],
        "timeline": [
            {
                "phase": "Staging & nettoyage",
                "detail": "Preparation des flux SSIS, normalisation et controle qualite.",
            },
            {
                "phase": "Data warehouse",
                "detail": "Modelisation des faits et dimensions sous SQL Server pour l'analyse.",
            },
            {
                "phase": "Restitution Power BI",
                "detail": "Construction du dashboard achats, ventes, marges et alertes.",
            },
            {
                "phase": "Prevision et ML",
                "detail": "Ajout d'une couche predictive pour anticiper la demande du mois suivant.",
            },
        ],
        "highlights": [
            "La page tableau de bord reste autonome et concentree sur son propre contenu.",
            "Le titre du dashboard et le filtre date ont maintenant une vraie place dans la structure.",
            "La prevision reste volontairement separee du dashboard pour garder un parcours logique.",
        ],
    }
    return render(request, "dashboard.html", context)


@login_required
def purchases_view(request):
    filiale = get_selected_filiale(request)
    if not filiale:
        return redirect("filiale_select")
    has_data = filiale_has_data(filiale)

    context = {
        "page_name": "purchases",
        "filiale": filiale,
        "filiale_has_data": has_data,
        "hero_title": f"Dashboard achats - {filiale.name}",
        "hero_text": (
            "Vue reservee au suivi des achats, des fournisseurs, des depenses et du budget avant "
            "integration du rapport Power BI final."
        ),
        "hero_meta": ["Pilotage achats", "Suivi fournisseurs", "Controle budgetaire"],
        "hero_actions": [
            {"label": "Aller vers ventes", "url_name": "sales", "kind": "primary"},
            {"label": "Retour accueil", "url_name": "dashboard", "kind": "secondary"},
        ],
        "kpis": [
            {"label": "Achats du mois", "value": "7.1 M TND", "trend": "+2.4%", "tone": "neutral", "detail": "Valeur globale des achats et commandes engages"},
            {"label": "Taux de couverture", "value": "89%", "trend": "+3 pt", "tone": "good", "detail": "Disponibilite des familles strategiques et reapprovisionnement"},
            {"label": "Delai moyen", "value": "5.6 j", "trend": "-0.8 j", "tone": "good", "detail": "Performance moyenne de livraison fournisseurs"},
            {"label": "Alertes budget", "value": "3", "trend": "A surveiller", "tone": "alert", "detail": "Depassements ou variations budgetaires detectes"},
        ],
        "dashboard_filters": ["Periode", "Famille achat", "Fournisseur", "Depot", "Centre de cout"],
        "embed_steps": [
            "Inserer le rapport achats Power BI ou l'iframe de test.",
            "Brancher les filtres budget, fournisseur et famille.",
            "Afficher alertes critiques et variations mensuelles.",
            "Relier la vue detaillee aux tables SQL Server du warehouse.",
        ],
        "module_cards": [
            {"title": "Depenses & budget", "text": "Lecture mensuelle des achats reels vs budget planifie."},
            {"title": "Suivi fournisseurs", "text": "Classement des partenaires par valeur, delai et fiabilite."},
            {"title": "Familles critiques", "text": "Focus sur les categories sensibles pour l'approvisionnement."},
        ],
        "journey_links": get_workspace_links(),
    }
    return render(request, "purchases.html", context)


@login_required
def sales_view(request):
    filiale = get_selected_filiale(request)
    if not filiale:
        return redirect("filiale_select")
    has_data = filiale_has_data(filiale)

    context = {
        "page_name": "sales",
        "filiale": filiale,
        "filiale_has_data": has_data,
        "hero_title": f"Dashboard ventes - {filiale.name}",
        "hero_text": (
            "Vue reservee au chiffre d'affaires, aux volumes, aux marges et a la performance "
            "commerciale avant integration du rapport ventes final."
        ),
        "hero_meta": ["Performance commerciale", "Lecture des marges", "Cockpit ventes"],
        "hero_actions": [
            {"label": "Retour espace filiale", "url_name": "workspace_home", "kind": "primary"},
            {"label": "Retour achats", "url_name": "purchases", "kind": "secondary"},
        ],
        "kpis": [
            {"label": "CA du mois", "value": "12.4 M TND", "trend": "+8.2%", "tone": "good", "detail": "Performance globale vs N-1"},
            {"label": "Volumes vendus", "value": "41 250 u", "trend": "+5.7%", "tone": "good", "detail": "Evolution mensuelle des quantites ecoulees"},
            {"label": "Marge brute", "value": "18.7%", "trend": "+1.3 pt", "tone": "good", "detail": "Lecture business de la rentabilite"},
            {"label": "Canal sensible", "value": "Modern trade", "trend": "Sous pression", "tone": "alert", "detail": "Circuit a surveiller pour les ruptures et promotions"},
        ],
        "dashboard_filters": ["Periode", "Canal", "Region", "Produit", "Famille"],
        "embed_steps": [
            "Inserer le rapport ventes Power BI ou la maquette interactive.",
            "Afficher les analyses par region, produit, famille et canal.",
            "Mesurer les variations de volume, CA et marge.",
            "Ajouter comparatifs reel vs objectif et alertes terrain.",
        ],
        "module_cards": [
            {"title": "CA & marge", "text": "Vue executive des revenus et de la profitabilite."},
            {"title": "Top produits", "text": "Identification des references locomotives et des baisses."},
            {"title": "Canaux & regions", "text": "Lecture de la performance par circuit et zone geographique."},
        ],
        "journey_links": get_workspace_links(),
    }
    return render(request, "sales.html", context)


@login_required
def forecast_view(request):
    filiale = get_selected_filiale(request)
    if not filiale:
        return redirect("filiale_select")
    has_data = filiale_has_data(filiale)

    if has_data:
        context = load_forecast_data()
        context["page_name"] = "forecast"
        return render(request, "prediction_results.html", context)

    context = {
        "page_name": "forecast",
        "filiale": filiale,
        "filiale_has_data": has_data,
        "hero_title": f"Prevision mensuelle - {filiale.name}",
        "hero_text": (
            "Espace reserve aux simulations, a la recherche multicritere et "
            "a la restitution des previsions generees par Python et Power BI."
        ),
        "hero_meta": [
            "Projection mensuelle",
            "Planification de scenarios",
            "Python dans Power BI",
        ],
        "hero_actions": [
            {"label": "Retour espace filiale", "url_name": "workspace_home", "kind": "secondary"},
            {"label": "Retour au choix du module", "url_name": "workspace_home", "kind": "primary"},
        ],
        "forecast_cards": [
            {
                "label": "Demande prevue",
                "value": "41 250 u",
                "detail": "Projection mensuelle basee sur les tendances glissantes et la saisonnalite.",
            },
            {
                "label": "CA previsionnel",
                "value": "13.1 M TND",
                "detail": "Simulation executive pour comparer le scenario central et les variantes.",
            },
            {
                "label": "Risque de rupture",
                "value": "6 categories",
                "detail": "Bloc ideal pour les alertes de stock et la priorisation des approvisionnements.",
            },
        ],
        "search_examples": ["Produit", "Famille", "Zone commerciale", "Periode", "Canal", "Segment client"],
        "forecast_filters": [
            "Mois de projection",
            "Famille produit",
            "Region",
            "Depot",
            "Scenario budgetaire",
        ],
        "model_blocks": [
            {
                "title": "Data set d'entree",
                "text": "Historique ventes, promotions, saisonnalite, stock, prix et evenements metiers.",
            },
            {
                "title": "Moteur de prevision",
                "text": "Script Python ou modele ML integre a Power BI pour produire la prediction du mois suivant.",
            },
            {
                "title": "Restitution",
                "text": "Visualisation du reel vs prevu, des ecarts et des alertes business.",
            },
        ],
        "forecast_results": [
            {
                "name": "Poulet entier",
                "confidence": "91%",
                "projection": "+6.8%",
                "decision": "Augmenter le stock securite sur Grand Tunis",
            },
            {
                "name": "Escalope marinee",
                "confidence": "87%",
                "projection": "+4.1%",
                "decision": "Renforcer la mise en avant promotionnelle",
            },
            {
                "name": "Aliments volailles",
                "confidence": "83%",
                "projection": "-2.3%",
                "decision": "Ajuster les achats et surveiller la marge",
            },
        ],
        "roadmap": [
            "Brancher le dataset de prevision issu de SQL Server ou du modele Python.",
            "Ajouter une recherche multicritere par produit, mois, region et canal.",
            "Afficher la comparaison reel vs prevu pour soutenir la decision.",
            "Prevoir l'export des recommandations a partager avec les responsables filiale.",
        ],
        "journey_links": get_workspace_links(),
    }
    return render(request, "forecast.html", context)


@login_required
def article_analysis_view(request):
    filiale = get_selected_filiale(request)
    if not filiale:
        return redirect("filiale_select")
    has_data = filiale_has_data(filiale)

    context = {
        "page_name": "article_analysis",
        "filiale": filiale,
        "filiale_has_data": has_data,
        "hero_title": f"Analyse article - {filiale.name}",
        "hero_text": "Analyse detaillee par article, categorie et performances produit.",
        "hero_meta": ["Analyse articles", "Performance produit", "Vue detaillee"],
    }
    return render(request, "article_analysis.html", context)


@admin_user_required
def admin_dashboard(request):
    context = {
        "page_name": "admin",
        "admin_links": get_admin_links(),
    }
    return render(request, "admin/dashboard.html", context)


@admin_user_required
def users_list(request):
    ensure_default_filiales()
    user_queryset = (
        User.objects.select_related("profile", "profile__filiale")
        .prefetch_related("profile__accessible_filiales")
        .order_by("-date_joined", "-id")
    )

    search_query = (request.GET.get("q") or "").strip()
    filiale_filter = (request.GET.get("filiale") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    access_filter = (request.GET.get("access") or "").strip()

    if search_query:
        user_queryset = user_queryset.filter(
            Q(username__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(profile__cin__icontains=search_query)
            | Q(profile__position__icontains=search_query)
        )

    if filiale_filter.isdigit():
        user_queryset = user_queryset.filter(
            Q(profile__filiale_id=filiale_filter) | Q(profile__accessible_filiales__id=filiale_filter)
        )

    if status_filter == "active":
        user_queryset = user_queryset.filter(is_active=True)
    elif status_filter == "inactive":
        user_queryset = user_queryset.filter(is_active=False)

    if role_filter == "admin":
        user_queryset = user_queryset.filter(is_superuser=True)
    elif role_filter == "staff":
        user_queryset = user_queryset.filter(is_staff=True, is_superuser=False)
    elif role_filter == "user":
        user_queryset = user_queryset.filter(is_staff=False, is_superuser=False)

    if access_filter == "assigned":
        user_queryset = user_queryset.filter(profile__accessible_filiales__isnull=False)
    elif access_filter == "unassigned":
        user_queryset = user_queryset.filter(profile__accessible_filiales__isnull=True)

    filtered_users = user_queryset.distinct()
    paginator = Paginator(filtered_users, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    all_users = User.objects.all()
    context = {
        "users": page_obj.object_list,
        "page_obj": page_obj,
        "total_users": all_users.count(),
        "active_users": all_users.filter(is_active=True).count(),
        "staff_users": all_users.filter(is_staff=True).count(),
        "admin_users": all_users.filter(is_superuser=True).count(),
        "filiales": get_filiale_catalog(),
        "filters": {
            "q": search_query,
            "filiale": filiale_filter,
            "status": status_filter,
            "role": role_filter,
            "access": access_filter,
        },
        "page_name": "users",
    }
    return render(request, "users/list.html", context)


@admin_user_required
def filiales_list(request):
    filiales_queryset = Filiale.objects.prefetch_related(
        "primary_users",
        "authorized_users",
    ).order_by("sort_order", "name")
    status_filter = (request.GET.get("status") or "").strip()
    search_query = (request.GET.get("q") or "").strip()

    if search_query:
        filiales_queryset = filiales_queryset.filter(
            Q(name__icontains=search_query) | Q(code__icontains=search_query) | Q(description__icontains=search_query)
        )

    if status_filter == "active":
        filiales_queryset = filiales_queryset.filter(is_active=True)
    elif status_filter == "inactive":
        filiales_queryset = filiales_queryset.filter(is_active=False)

    filiales = list(filiales_queryset)
    for filiale in filiales:
        filiale.primary_user_count = filiale.primary_users.count()
        filiale.authorized_user_count = filiale.authorized_users.count()

    context = {
        "page_name": "filiales_admin",
        "filiales": filiales,
        "total_filiales": Filiale.objects.count(),
        "active_filiales": Filiale.objects.filter(is_active=True).count(),
        "inactive_filiales": Filiale.objects.filter(is_active=False).count(),
        "filters": {"q": search_query, "status": status_filter},
    }
    return render(request, "filiales/list.html", context)


@admin_user_required
@require_http_methods(["GET", "POST"])
def filiales_create(request):
    form = FilialeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La filiale a ete creee avec succes.")
        return redirect("filiales_list")
    return render(
        request,
        "filiales/form.html",
        {
            "form": form,
            "page_name": "filiales_admin",
            "page_title": "Creer une filiale",
            "page_intro": "Ajoute une nouvelle filiale avec son code, sa description, son statut et son ordre d'affichage.",
            "submit_label": "Creer la filiale",
        },
    )


@admin_user_required
@require_http_methods(["GET", "POST"])
def filiales_update(request, filiale_id):
    filiale = get_object_or_404(Filiale, pk=filiale_id)
    form = FilialeForm(request.POST or None, instance=filiale)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "La filiale a ete mise a jour avec succes.")
        return redirect("filiales_list")
    return render(
        request,
        "filiales/form.html",
        {
            "form": form,
            "filiale": filiale,
            "page_name": "filiales_admin",
            "page_title": f"Modifier la filiale : {filiale.name}",
            "page_intro": "Mets a jour le libelle, le code, l'ordre d'affichage et le statut de disponibilite.",
            "submit_label": "Enregistrer les modifications",
            "edit_mode": True,
        },
    )


@admin_user_required
@require_http_methods(["GET", "POST"])
def filiales_delete(request, filiale_id):
    filiale = get_object_or_404(Filiale, pk=filiale_id)
    primary_user_count = filiale.primary_users.count()
    authorized_user_count = filiale.authorized_users.count()

    if request.method == "POST":
        deleted_label = filiale.name
        filiale.delete()
        messages.success(request, f"La filiale {deleted_label} a ete supprimee avec succes.")
        return redirect("filiales_list")

    return render(
        request,
        "filiales/confirm_delete.html",
        {
            "page_name": "filiales_admin",
            "filiale": filiale,
            "primary_user_count": primary_user_count,
            "authorized_user_count": authorized_user_count,
        },
    )


@admin_user_required
def users_detail(request, user_id):
    user_obj = get_object_or_404(
        User.objects.select_related("profile", "profile__filiale").prefetch_related("profile__accessible_filiales"),
        pk=user_id,
    )
    profile = get_user_profile(user_obj)
    user_obj.refresh_from_db()
    return render(
        request,
        "users/detail.html",
        {
            "user_obj": user_obj,
            "profile_obj": profile,
            "page_name": "users",
        },
    )


@admin_user_required
@require_http_methods(["GET", "POST"])
def users_create(request):
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                form.save_profile(user)
            messages.success(request, "Le compte utilisateur a ete cree avec succes.")
            return redirect("users_detail", user_id=user.id)
    else:
        form = UserCreateForm()
    return render(
        request,
        "users/form.html",
        {
            "form": form,
            "page_title": "Creer un utilisateur",
            "page_intro": "Creation d'un compte, attribution de la filiale et definition des droits d'acces.",
            "submit_label": "Creer l'utilisateur",
            "page_name": "users",
        },
    )


@admin_user_required
@require_http_methods(["GET", "POST"])
def users_update(request, user_id):
    user_obj = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=user_obj)
        if form.is_valid():
            with transaction.atomic():
                updated_user = form.save()
                form.save_profile(updated_user)
            messages.success(request, "Le compte utilisateur a ete mis a jour avec succes.")
            return redirect("users_detail", user_id=updated_user.id)
    else:
        form = UserUpdateForm(instance=user_obj)
    return render(
        request,
        "users/form.html",
        {
            "form": form,
            "page_title": f"Modifier l'utilisateur : {user_obj.username}",
            "page_intro": "Mise a jour des informations du compte, du profil metier et des permissions.",
            "submit_label": "Enregistrer les modifications",
            "edit_mode": True,
            "user_obj": user_obj,
            "page_name": "users",
        },
    )


@admin_user_required
@require_http_methods(["GET", "POST"])
def users_delete(request, user_id):
    user_obj = get_object_or_404(User, pk=user_id)
    if user_obj == request.user:
        messages.error(request, "Vous ne pouvez pas supprimer votre propre compte.")
        return redirect("users_list")

    if user_obj.is_staff and not User.objects.filter(is_staff=True, is_active=True).exclude(pk=user_obj.pk).exists():
        messages.error(request, "Au moins un compte staff actif doit rester disponible.")
        return redirect("users_detail", user_id=user_obj.pk)

    if request.method == "POST":
        deleted_label = user_obj.get_full_name() or user_obj.username
        user_obj.delete()
        messages.success(request, f"Le compte {deleted_label} a ete supprime avec succes.")
        return redirect("users_list")

    return render(request, "users/confirm_delete.html", {"user_obj": user_obj, "page_name": "users"})


@login_required
@require_http_methods(["GET", "POST"])
def account_password_change_view(request):
    form = AccountPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Votre mot de passe a ete modifie avec succes.")
        return redirect("account_password_change")
    return render(request, "account/password_change.html", {"form": form, "page_name": "account_password_change"})


@login_required
def account_profile_view(request):
    return render(
        request,
        "account/profile.html",
        {
            "page_name": "account_profile",
            "profile_obj": get_user_profile(request.user),
        },
    )


@login_required
def account_settings_view(request):
    return render(
        request,
        "account/settings.html",
        {
            "page_name": "account_settings",
        },
    )


@login_required
def ml_predict_view(request):
    result = None
    form = MLForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        input_data = form.cleaned_data["input_data"].strip()
        result = f"Donnees recues: {input_data}"

    context = {
        "page_name": "ml_predict",
        "form": form,
        "result": result,
        "hero_title": "Prédiction ML",
        "hero_text": "Entrez des données pour obtenir une prédiction via le modèle de ML.",
    }
    return render(request, "prediction_form.html", context)
