import hashlib
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Filiale(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.SlugField(max_length=60, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    cin = models.CharField(max_length=20, unique=True)
    filiale = models.ForeignKey(
        Filiale,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="primary_users",
    )
    accessible_filiales = models.ManyToManyField(Filiale, blank=True, related_name="authorized_users")
    phone = models.CharField(max_length=20, blank=True)
    position = models.CharField(max_length=120, blank=True)
    is_filiale_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "cin"]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.cin})"


class PasswordResetCode(models.Model):
    CODE_VALIDITY_MINUTES = 10
    RESEND_COOLDOWN_SECONDS = 60
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_codes")
    code = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_verified = models.BooleanField(default=False)
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash_code(raw_code):
        return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()

    @classmethod
    def build_for_user(cls, user, raw_code, validity_minutes=None):
        if validity_minutes is None:
            validity_minutes = cls.CODE_VALIDITY_MINUTES
        return cls(
            user=user,
            code=cls.hash_code(raw_code),
            expires_at=timezone.now() + timedelta(minutes=validity_minutes),
        )

    def is_expired(self):
        return timezone.now() > self.expires_at

    def matches(self, raw_code):
        return self.code == self.hash_code(raw_code)
