from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

from .validators import normalize_email


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        login_value = username or kwargs.get(UserModel.USERNAME_FIELD)
        if not login_value or not password:
            return None

        login_value = login_value.strip()
        normalized_email = normalize_email(login_value)

        user = (
            UserModel._default_manager.filter(
                Q(email__iexact=normalized_email or login_value)
                | Q(username__iexact=login_value)
            )
            .order_by("id")
            .first()
        )

        if user is None or not user.check_password(password):
            return None

        if not self.user_can_authenticate(user):
            return None

        return user
