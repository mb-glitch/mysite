# ==============================================================================
# monitoring/admin.py
# Aplikacja: MONITORING
# Panel Service Accounts i historii logów
# ==============================================================================
from django.contrib import admin
from .models import MonitoredService, LogEntry


class LogEntryInline(admin.TabularInline):
    model = LogEntry
    extra = 0
    ordering = ('-checked_at',)
    readonly_fields = ('status_code', 'checked_at', 'message')
    can_delete = False
    max_num = 15

    def has_add_permission(self, request, obj=None):
        return False

# monitoring/admin.py
@admin.register(MonitoredService)
class MonitoredServiceAdmin(admin.ModelAdmin):
    list_display = (
        'service_name',
        'get_service_user',
        'drf_token_display',
        'get_owner',
        'status_display',
        'last_status_display',
        'is_active',
        'last_check_at',
    )

    def get_service_user(self, obj):
        return obj.service_user.username if obj.service_user else "(brak konta maszynowego)"
    get_service_user.short_description = "Konto maszynowe (DRF)"

    def get_owner(self, obj):
        return obj.user.username if obj.user else "(brak)"
    get_owner.short_description = "Opiekun (użytkownik)"

    def drf_token_display(self, obj):
        t = obj.token
        return f"{t[:10]}..." if t else "-"
    drf_token_display.short_description = "Token DRF"
