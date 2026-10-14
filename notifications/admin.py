from django.contrib import admin
from .models import PushDelivery, PushDevice


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    fields = ('id', 'user', 'is_active', 'updated_at')
    readonly_fields = ('id', 'user', 'updated_at')

    def has_add_permission(self, request):
        return False


@admin.register(PushDelivery)
class PushDeliveryAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'group', 'state', 'attempts', 'created_at', 'sent_at', 'last_error_code')
    list_filter = ('group', 'state')
    fields = readonly_fields = ('id', 'device', 'user', 'event_key', 'group', 'title', 'body',
                               'state', 'attempts', 'created_at', 'sent_at', 'retry_at', 'last_error_code')

    def has_add_permission(self, request):
        return False
