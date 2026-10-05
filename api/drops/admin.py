from django.contrib import admin

from .models import Drop, Report, SiteStatus


@admin.register(Drop)
class DropAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "status", "is_public", "is_anonymous", "created_at"]
    list_filter = ["status", "is_public", "is_anonymous"]
    search_fields = ["id", "title"]


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """The moderation queue. Reports carry no reporter identity by design."""
    list_display = ["id", "drop", "reason", "status", "created_at"]
    list_filter = ["reason", "status"]


@admin.register(SiteStatus)
class SiteStatusAdmin(admin.ModelAdmin):
    """The kill switch. Set cooling_until to a future time to pause the
    whole instance; it resumes automatically the moment that time passes
    — nothing to remember to toggle back."""

    list_display = ["cooling_until", "is_cooling", "cooling_message"]

    def has_add_permission(self, request):
        return not SiteStatus.objects.exists()  # singleton — one row, ever

    def has_delete_permission(self, request, obj=None):
        return False
