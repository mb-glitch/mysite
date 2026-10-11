# ==============================================================================
# monitoring/admin.py
# Aplikacja: MONITORING
# Panel Service Accounts i historii logów
# ==============================================================================
from django.contrib import admin
from django import forms
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils import timezone
from .models import MonitoredService, LogEntry


class MonitoredServiceForm(forms.ModelForm):
    token_enabled = forms.BooleanField(
        required=False, initial=True, label='Token aktywny',
        help_text='Wyłączenie dezaktywuje konto techniczne i wszystkie żądania z jego tokenem DRF.',
    )

    class Meta:
        model = MonitoredService
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.service_user_id:
            self.fields['token_enabled'].initial = self.instance.service_user.is_active


@admin.register(MonitoredService)
class MonitoredServiceAdmin(admin.ModelAdmin):
    form = MonitoredServiceForm
    list_display = (
        'service_name',
        'service_user',
        'drf_token_display',
        'owner',
        'status_display',
        'last_status_display',
        'is_active',
        'token_enabled',
        'last_check_at',
    )
    list_filter = ('is_active', 'service_name', 'last_status', 'owner')
    search_fields = ('service_name', 'service_user__username', 'owner__username', 'description')
    readonly_fields = ('recent_logs',)

    @admin.display(description='Ostatnie 15 raportów')
    def recent_logs(self, obj):
        if not obj.pk:
            return 'Brak raportów.'
        rows = format_html_join(
            '', '<tr><td>{}</td><td>{}</td><td><pre>{}</pre></td></tr>',
            ((timezone.localtime(log.checked_at), log.status_code, log.message)
             for log in obj.logs.order_by('-checked_at', '-pk')[:15]),
        )
        url = reverse('admin:monitoring_logentry_changelist')
        return format_html(
            '<table><thead><tr><th>Data</th><th>Status</th><th>Dane</th></tr></thead>'
            '<tbody>{}</tbody></table><a href="{}?service__id__exact={}">Pełna historia raportów</a>',
            rows, url, obj.pk,
        )

    @admin.display(boolean=True, description='Token aktywny')
    def token_enabled(self, obj):
        return bool(obj.service_user and obj.service_user.is_active)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.service_user:
            obj.service_user.is_active = form.cleaned_data['token_enabled']
            obj.service_user.save(update_fields=['is_active'])
            from rest_framework.authtoken.models import Token
            Token.objects.get_or_create(user=obj.service_user)

    def drf_token_display(self, obj):
        return f"{obj.token[:10]}..." if obj.token else "-"
    drf_token_display.short_description = "Token DRF Konta Maszynowego"


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ('service', 'status_code', 'checked_at')
    list_filter = ('service', 'status_code')
    ordering = ('-checked_at', '-pk')
    list_per_page = 50
    readonly_fields = ('service', 'status_code', 'checked_at', 'message')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

