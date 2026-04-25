from django.contrib import admin
from .models import MonitoredService, LogEntry


class LogEntryInline(admin.TabularInline):
    model = LogEntry
    extra = 0
    ordering = ('-checked_at',)
    readonly_fields = ('status', 'status_code', 'response_time_ms', 'checked_at', 'message')
    can_delete = False
    max_num = 5  # 👈 tylko kilka ostatnich

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MonitoredService)
class MonitoredServiceAdmin(admin.ModelAdmin):
    list_display = ('user__username', 'is_active')
    search_fields = ('user__username',)
    inlines = [LogEntryInline]
