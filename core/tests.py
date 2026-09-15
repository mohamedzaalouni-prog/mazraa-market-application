import re

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Filiale, PasswordResetCode, UserProfile
from .views import ensure_default_filiales


class AdminUserManagementTests(TestCase):
    ADMIN_CIN = "81000001"
    ADMIN_PASSWORD = "TestAdminPass123!"
    USER_CIN = "81000002"
    USER_PASSWORD = "TestUserPass123!"
    MANAGED_CIN = "81000003"
    MANAGED_PASSWORD = "TestManagedPass123!"

    @classmethod
    def setUpTestData(cls):
        ensure_default_filiales()
        cls.mazraa = Filiale.objects.get(code="mazraa-market")
        cls.gipa = Filiale.objects.get(code="gipa")

        cls.admin_user = User.objects.create_user(
            username=cls.ADMIN_CIN,
            password=cls.ADMIN_PASSWORD,
            email="admin@app.local",
            first_name="Admin",
            last_name="Principal",
            is_staff=True,
            is_superuser=True,
        )
        admin_profile = UserProfile.objects.create(
            user=cls.admin_user,
            cin=cls.ADMIN_CIN,
            filiale=cls.mazraa,
            is_filiale_admin=True,
            phone="70000001",
            position="Directeur SI",
        )
        admin_profile.accessible_filiales.set([cls.mazraa, cls.gipa])

        cls.normal_user = User.objects.create_user(
            username=cls.USER_CIN,
            password=cls.USER_PASSWORD,
            email="user@app.local",
            first_name="User",
            last_name="Standard",
        )
        user_profile = UserProfile.objects.create(
            user=cls.normal_user,
            cin=cls.USER_CIN,
            filiale=cls.mazraa,
            phone="70000002",
            position="Analyste",
        )
        user_profile.accessible_filiales.set([cls.mazraa])

        cls.managed_user = User.objects.create_user(
            username=cls.MANAGED_CIN,
            password=cls.MANAGED_PASSWORD,
            email="managed@app.local",
            first_name="Sara",
            last_name="Gestion",
            is_staff=False,
            is_active=True,
        )
        managed_profile = UserProfile.objects.create(
            user=cls.managed_user,
            cin=cls.MANAGED_CIN,
            filiale=cls.mazraa,
            phone="70000003",
            position="Chef projet",
            is_filiale_admin=False,
        )
        managed_profile.accessible_filiales.set([cls.mazraa])

    def login_admin(self):
        self.client.login(username=self.admin_user.email, password=self.ADMIN_PASSWORD)

    def login_normal_user(self):
        self.client.login(username=self.normal_user.email, password=self.USER_PASSWORD)

    def test_anonymous_user_is_redirected_to_login_for_admin_pages(self):
        response = self.client.get(reverse("users_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_normal_user_cannot_access_admin_pages_or_actions(self):
        self.login_normal_user()

        list_response = self.client.get(reverse("users_list"))
        create_response = self.client.post(
            reverse("users_create"),
            {
                "cin": "44444444",
                "first_name": "No",
                "last_name": "Access",
                "email": "blocked@app.local",
                "password1": "BlockedPass123!",
                "password2": "BlockedPass123!",
                "is_active": "on",
            },
        )
        detail_response = self.client.get(reverse("users_detail", args=[self.managed_user.pk]))

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(detail_response.status_code, 403)
        self.assertFalse(User.objects.filter(username="44444444").exists())

    def test_admin_can_access_list_and_detail_pages(self):
        self.login_admin()

        dashboard_response = self.client.get(reverse("admin_dashboard"))
        list_response = self.client.get(reverse("users_list"))
        detail_response = self.client.get(reverse("users_detail", args=[self.managed_user.pk]))

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Interface admin")
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Admin utilisateurs")
        self.assertContains(list_response, "shell-header")
        self.assertContains(list_response, self.MANAGED_CIN)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Chef projet")
        self.assertContains(detail_response, "Rattachement et acces")

    def test_admin_can_list_users_with_filters(self):
        self.login_admin()

        response = self.client.get(reverse("users_list"), {"q": "Sara", "role": "user", "status": "active"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.MANAGED_CIN)
        self.assertNotContains(response, self.ADMIN_CIN)

    def test_admin_can_create_user_and_profile_with_hashed_password(self):
        self.login_admin()

        response = self.client.post(
            reverse("users_create"),
            {
                "cin": "44444444",
                "first_name": "Nadia",
                "last_name": "Support",
                "email": "nadia@app.local",
                "filiale": str(self.gipa.pk),
                "accessible_filiales": [str(self.gipa.pk), str(self.mazraa.pk)],
                "phone": "70123456",
                "position": "Support metier",
                "is_filiale_admin": "on",
                "is_staff": "on",
                "is_superuser": "",
                "is_active": "on",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
            },
            follow=True,
        )

        created_user = User.objects.get(username="44444444")
        created_profile = created_user.profile

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "a ete cree avec succes")
        self.assertNotEqual(created_user.password, "SecurePass123!")
        self.assertTrue(created_user.check_password("SecurePass123!"))
        self.assertEqual(created_profile.cin, "44444444")
        self.assertEqual(created_profile.filiale, self.gipa)
        self.assertEqual(created_profile.phone, "70123456")
        self.assertEqual(created_profile.position, "Support metier")
        self.assertTrue(created_profile.is_filiale_admin)
        self.assertSetEqual(
            set(created_profile.accessible_filiales.values_list("code", flat=True)),
            {"gipa", "mazraa-market"},
        )

    def test_duplicate_validation_blocks_reused_cin_and_email(self):
        self.login_admin()

        response = self.client.post(
            reverse("users_create"),
            {
                "cin": self.MANAGED_CIN,
                "first_name": "Dup",
                "last_name": "User",
                "email": "managed@app.local",
                "password1": "SecurePass123!",
                "password2": "SecurePass123!",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ce CIN existe deja.")
        self.assertContains(response, "Cette adresse email est deja utilisee.")

    def test_admin_can_update_user_and_profile(self):
        self.login_admin()

        response = self.client.post(
            reverse("users_update", args=[self.managed_user.pk]),
            {
                "cin": self.MANAGED_CIN,
                "first_name": "Sara",
                "last_name": "Gestion",
                "email": "managed-updated@app.local",
                "filiale": str(self.gipa.pk),
                "accessible_filiales": [str(self.gipa.pk)],
                "phone": "70999888",
                "position": "Responsable operations",
                "is_filiale_admin": "on",
                "is_staff": "on",
                "is_superuser": "",
                "is_active": "on",
                "new_password1": "UpdatedPass123!",
                "new_password2": "UpdatedPass123!",
            },
            follow=True,
        )

        self.managed_user.refresh_from_db()
        updated_profile = self.managed_user.profile

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "a ete mis a jour avec succes")
        self.assertEqual(self.managed_user.email, "managed-updated@app.local")
        self.assertTrue(self.managed_user.is_staff)
        self.assertTrue(self.managed_user.check_password("UpdatedPass123!"))
        self.assertEqual(updated_profile.filiale, self.gipa)
        self.assertEqual(updated_profile.phone, "70999888")
        self.assertEqual(updated_profile.position, "Responsable operations")
        self.assertTrue(updated_profile.is_filiale_admin)
        self.assertSetEqual(set(updated_profile.accessible_filiales.values_list("code", flat=True)), {"gipa"})

    def test_admin_delete_requires_confirmation_and_removes_user(self):
        self.login_admin()

        confirm_response = self.client.get(reverse("users_delete", args=[self.managed_user.pk]))
        delete_response = self.client.post(reverse("users_delete", args=[self.managed_user.pk]), follow=True)

        self.assertEqual(confirm_response.status_code, 200)
        self.assertContains(confirm_response, "Suppression definitive")
        self.assertEqual(delete_response.status_code, 200)
        self.assertContains(delete_response, "a ete supprime avec succes")
        self.assertFalse(User.objects.filter(pk=self.managed_user.pk).exists())

    def test_staff_login_redirects_to_admin_dashboard(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.admin_user.email.upper(), "password": self.ADMIN_PASSWORD},
        )

        self.assertRedirects(response, reverse("admin_dashboard"))

    def test_normal_login_redirects_to_filiale_selection(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.normal_user.email, "password": self.USER_PASSWORD},
        )

        self.assertRedirects(response, reverse("filiale_select"))

    def test_admin_can_manage_filiales(self):
        self.login_admin()

        update_response = self.client.post(
            reverse("filiales_update", args=[self.gipa.pk]),
            {
                "name": "GIPA",
                "code": "gipa",
                "description": "Filiale a venir",
                "sort_order": 2,
                "is_active": "",
            },
            follow=True,
        )
        list_response = self.client.get(reverse("filiales_list"))
        create_response = self.client.post(
            reverse("filiales_create"),
            {
                "name": "Nord Hub",
                "code": "nord-hub",
                "description": "Plateforme regionale nord",
                "sort_order": 4,
                "is_active": "on",
            },
            follow=True,
        )
        delete_response = self.client.post(reverse("filiales_delete", args=[self.gipa.pk]), follow=True)

        self.assertEqual(update_response.status_code, 200)
        self.assertContains(update_response, "a ete mise a jour avec succes")
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Admin filiales")
        self.assertContains(list_response, "GIPA")
        self.assertEqual(create_response.status_code, 200)
        self.assertContains(create_response, "a ete creee avec succes")
        self.assertTrue(Filiale.objects.filter(code="nord-hub").exists())
        self.assertEqual(delete_response.status_code, 200)
        self.assertContains(delete_response, "a ete supprimee avec succes")
        self.assertFalse(Filiale.objects.filter(code="gipa").exists())

    def test_normal_user_cannot_access_filiale_admin_pages(self):
        self.login_normal_user()

        list_response = self.client.get(reverse("filiales_list"))
        create_response = self.client.post(
            reverse("filiales_create"),
            {
                "name": "Blocked Filiale",
                "code": "blocked-filiale",
                "sort_order": 9,
                "is_active": "on",
            },
        )

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)
        self.assertFalse(Filiale.objects.filter(code="blocked-filiale").exists())

    def test_only_mazraa_is_selectable_and_other_default_filiales_are_future(self):
        self.login_admin()

        response = self.client.get(reverse("filiale_select"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MAZRAA MARKET")
        self.assertContains(response, "GIPA")
        self.assertContains(response, "JADIDA")
        self.assertNotContains(response, 'value="%s"' % self.gipa.pk)
        self.assertNotContains(response, "Sud Hub")

    def test_account_profile_page_shows_profile_information(self):
        self.login_admin()

        response = self.client.get(reverse("account_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informations principales")
        self.assertContains(response, "Perimetre fonctionnel")
        self.assertNotContains(response, "Centre de compte")

    def test_account_menu_no_longer_shows_change_email_link(self):
        self.login_admin()

        response = self.client.get(reverse("account_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Changer le mot de passe")
        self.assertNotContains(response, "Changer l'email")

    def test_front_office_email_change_route_is_not_available(self):
        self.login_normal_user()

        response = self.client.get("/account/email-change/")

        self.assertEqual(response.status_code, 404)

    def test_django_admin_login_page_uses_email_or_identifier_label(self):
        response = self.client.get(reverse("admin:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email ou identifiant")

    def test_django_admin_accepts_email_login_case_insensitively(self):
        response = self.client.post(
            reverse("admin:login"),
            {
                "username": self.admin_user.email.upper(),
                "password": self.ADMIN_PASSWORD,
                "next": reverse("admin:index"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], reverse("admin:index"))

    def test_django_admin_still_accepts_username_for_backward_compatibility(self):
        response = self.client.post(
            reverse("admin:login"),
            {
                "username": self.ADMIN_CIN,
                "password": self.ADMIN_PASSWORD,
                "next": reverse("admin:index"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request["PATH_INFO"], reverse("admin:index"))

    def test_django_admin_rejects_non_staff_user(self):
        response = self.client.post(
            reverse("admin:login"),
            {
                "username": self.normal_user.email,
                "password": self.USER_PASSWORD,
                "next": reverse("admin:index"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Veuillez saisir un email ou identifiant et un mot de passe valides")

    def test_django_admin_rejects_inactive_staff_user(self):
        inactive_staff = User.objects.create_user(
            username="81000009",
            password="InactiveStaff123!",
            email="inactive-staff@app.local",
            first_name="Inactive",
            last_name="Staff",
            is_staff=True,
            is_active=False,
        )
        UserProfile.objects.create(
            user=inactive_staff,
            cin="81000009",
            filiale=self.mazraa,
        )

        response = self.client.post(
            reverse("admin:login"),
            {
                "username": inactive_staff.email,
                "password": "InactiveStaff123!",
                "next": reverse("admin:index"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Veuillez saisir un email ou identifiant et un mot de passe valides")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.com",
    SITE_BASE_URL="http://testserver",
)
class AccountEmailFlowTests(TestCase):
    SIGNUP_CIN = "82000001"
    RESET_CIN = "82000002"
    INACTIVE_CIN = "82000003"

    @classmethod
    def setUpTestData(cls):
        ensure_default_filiales()
        cls.mazraa = Filiale.objects.get(code="mazraa-market")
        cls.reset_user = User.objects.create_user(
            username=cls.RESET_CIN,
            password="InitialPass123!",
            email="reset-user@app.local",
            first_name="Reset",
            last_name="User",
            is_active=True,
        )
        profile = UserProfile.objects.create(
            user=cls.reset_user,
            cin=cls.RESET_CIN,
            filiale=cls.mazraa,
        )
        profile.accessible_filiales.set([cls.mazraa])
        cls.inactive_user = User.objects.create_user(
            username=cls.INACTIVE_CIN,
            password="InactivePass123!",
            email="inactive-user@app.local",
            first_name="Inactive",
            last_name="User",
            is_active=False,
        )
        inactive_profile = UserProfile.objects.create(
            user=cls.inactive_user,
            cin=cls.INACTIVE_CIN,
            filiale=cls.mazraa,
        )
        inactive_profile.accessible_filiales.set([cls.mazraa])

    def extract_activation_path(self, email_body):
        match = re.search(r"http://testserver(?P<path>/activate-account/[^\s]+/[^/\s]+/)", email_body)
        self.assertIsNotNone(match)
        return match.group("path")

    def extract_reset_code(self, email_body):
        match = re.search(r"\b(\d{6})\b", email_body)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_signup_creates_inactive_user_and_activates_from_email_link(self):
        response = self.client.post(
            reverse("signup"),
            {
                "filiale": str(self.mazraa.pk),
                "cin": self.SIGNUP_CIN,
                "first_name": "Nour",
                "last_name": "Email",
                "email": "signup-user@app.local",
                "password1": "SignupPass123!",
                "password2": "SignupPass123!",
            },
        )

        created_user = User.objects.get(username=self.SIGNUP_CIN)

        self.assertRedirects(response, reverse("signup_pending"))
        self.assertFalse(created_user.is_active)
        self.assertEqual(len(mail.outbox), 1)

        activation_response = self.client.get(self.extract_activation_path(mail.outbox[0].body), follow=True)

        created_user.refresh_from_db()
        self.assertTrue(created_user.is_active)
        self.assertEqual(activation_response.status_code, 200)
        self.assertContains(activation_response, "Compte active")

    def test_user_can_log_in_with_email_only(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.reset_user.email.upper(),
                "password": "InitialPass123!",
            },
        )

        self.assertRedirects(response, reverse("filiale_select"))

    def test_user_cannot_log_in_with_cin_anymore(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.RESET_CIN,
                "password": "InitialPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saisissez une adresse de courriel valide.")

    def test_inactive_user_cannot_log_in(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.inactive_user.email,
                "password": "InactivePass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ce compte est inactif")

    def test_signup_rejects_malicious_name(self):
        response = self.client.post(
            reverse("signup"),
            {
                "filiale": str(self.mazraa.pk),
                "cin": "82000010",
                "first_name": "<script>alert(1)</script>",
                "last_name": "Martin",
                "email": "safe-name@app.local",
                "password1": "SignupPass123!",
                "password2": "SignupPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nom ne peut contenir que des lettres")
        self.assertFalse(User.objects.filter(email="safe-name@app.local").exists())

    def test_signup_rejects_duplicate_email(self):
        response = self.client.post(
            reverse("signup"),
            {
                "filiale": str(self.mazraa.pk),
                "cin": "82000011",
                "first_name": "Nour",
                "last_name": "Doublon",
                "email": self.reset_user.email.upper(),
                "password1": "SignupPass123!",
                "password2": "SignupPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cette adresse email est deja utilisee.")

    def test_signup_rejects_disposable_email(self):
        response = self.client.post(
            reverse("signup"),
            {
                "filiale": str(self.mazraa.pk),
                "cin": "82000012",
                "first_name": "Nour",
                "last_name": "Temp",
                "email": "user@mailinator.com",
                "password1": "SignupPass123!",
                "password2": "SignupPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Les adresses email temporaires ne sont pas autorisees.")

    def test_password_reset_flow_updates_password_with_single_active_code(self):
        first_request = self.client.post(
            reverse("password_reset"),
            {"email": self.reset_user.email},
        )
        second_request = self.client.post(
            reverse("password_reset"),
            {"email": self.reset_user.email},
        )

        self.assertRedirects(first_request, reverse("password_reset_verify"))
        self.assertRedirects(second_request, reverse("password_reset_verify"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            PasswordResetCode.objects.filter(user=self.reset_user, is_used=False).count(),
            1,
        )

        verify_response = self.client.post(
            reverse("password_reset_verify"),
            {
                "email": self.reset_user.email,
                "code": self.extract_reset_code(mail.outbox[0].body),
                "new_password1": "UpdatedPass123!",
                "new_password2": "UpdatedPass123!",
            },
            follow=True,
        )

        self.reset_user.refresh_from_db()
        reset_code = PasswordResetCode.objects.get(user=self.reset_user)
        self.assertEqual(verify_response.status_code, 200)
        self.assertTrue(self.reset_user.check_password("UpdatedPass123!"))
        self.assertTrue(reset_code.is_verified)
        self.assertTrue(reset_code.is_used)
