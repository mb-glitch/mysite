from django.contrib import admin
from .models import PushDelivery, PushDevice
from .services import retry_failed


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
    list_display = ('id', 'recipient_name', 'channel', 'group', 'state', 'attempts', 'created_at', 'sent_at', 'last_error_code')
    list_filter = ('channel', 'group', 'state')
    search_fields = ('recipient_name', 'event_key', 'title')
    fields = readonly_fields = ('id', 'device', 'user', 'recipient_name', 'recipient_email', 'channel',
                               'event_key', 'group', 'title', 'body', 'state', 'attempts', 'created_at',
                               'sent_at', 'retry_at', 'claimed_at', 'last_error_code')
    actions = ['retry_notifications']

    @admin.action(description='Ponów zaznaczone powiadomienia z błędem', permissions=['change'])
    def retry_notifications(self, request, queryset):
        self.message_user(request, f'Dodano do ponowienia: {retry_failed(queryset)}. Wysyłkę wykona cron.')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
