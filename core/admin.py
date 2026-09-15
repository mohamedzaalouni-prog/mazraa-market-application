from django.contrib import admin

from .admin_forms import AdminEmailAuthenticationForm
from .models import Filiale, UserProfile

admin.site.login_form = AdminEmailAuthenticationForm


@admin.register(Filiale)
class FilialeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "cin", "filiale", "is_filiale_admin", "updated_at")
    list_filter = ("is_filiale_admin", "filiale")
    search_fields = ("user__username", "user__first_name", "user__last_name", "cin")
    filter_horizontal = ("accessible_filiales",)
