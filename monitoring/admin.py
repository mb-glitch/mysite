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


@admin.register(MonitoredService)
class MonitoredServiceAdmin(admin.ModelAdmin):
    list_display = (
        'service_name',
        'service_user',
        'drf_token_display',
        'owner',
        'status_display',
        'last_status_display',
        'is_active',
        'last_check_at',
    )
    list_filter = ('is_active', 'service_name', 'last_status', 'owner')
    search_fields = ('service_name', 'service_user__username', 'owner__username', 'description')
    inlines = [LogEntryInline]

    def drf_token_display(self, obj):
        return f"{obj.token[:10]}..." if obj.token else "-"
    drf_token_display.short_description = "Token DRF Konta Maszynowego"

