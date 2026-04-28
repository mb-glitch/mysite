from django.contrib import admin
from .models import MonitoredService, LogEntry


class LogEntryInline(admin.TabularInline):
    model = LogEntry
    extra = 0
    ordering = ('-checked_at',)
    readonly_fields = ('status_code', 'checked_at', 'message')
    can_delete = False
    max_num = 5  # 👈 tylko kilka ostatnich

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MonitoredService)
class MonitoredServiceAdmin(admin.ModelAdmin):
    list_display = ('user__username', 'is_active', 'status_display', 'last_check_at', 'last_change_at',)
    search_fields = ('user__username',)
    inlines = [LogEntryInline]
