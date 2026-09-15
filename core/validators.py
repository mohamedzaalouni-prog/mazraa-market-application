import re

from django.core.exceptions import ValidationError


DISPOSABLE_EMAIL_DOMAINS = {
    "10minutemail.com",
    "getnada.com",
    "guerrillamail.com",
    "mailinator.com",
    "sharklasers.com",
    "temp-mail.org",
    "tempmail.com",
    "trashmail.com",
    "yopmail.com",
}

PERSON_NAME_PATTERN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ]+(?:[ '\-][A-Za-zÀ-ÖØ-öø-ÿ]+)*$")


def normalize_email(value):
    return (value or "").strip().lower()


def validate_not_disposable_email(email):
    normalized_email = normalize_email(email)
    if "@" not in normalized_email:
        return normalized_email

    domain = normalized_email.rsplit("@", 1)[1]
    for blocked_domain in DISPOSABLE_EMAIL_DOMAINS:
        if domain == blocked_domain or domain.endswith(f".{blocked_domain}"):
            raise ValidationError("Les adresses email temporaires ne sont pas autorisees.")
    return normalized_email


def clean_person_name(value, label):
    cleaned_value = " ".join((value or "").strip().split())
    if len(cleaned_value) < 2:
        raise ValidationError(f"{label} doit contenir au moins 2 caracteres.")
    if len(cleaned_value) > 150:
        raise ValidationError(f"{label} est trop long.")
    if not PERSON_NAME_PATTERN.fullmatch(cleaned_value):
        raise ValidationError(
            f"{label} ne peut contenir que des lettres, espaces, tirets et apostrophes."
        )
    return cleaned_value
