# ==============================================================================
# backupapp/admin.py
# Aplikacja: BACKUPAPP
# ==============================================================================
from django import forms
from django.contrib import admin
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework.authtoken.models import Token

from monitoring.models import MonitoredService
from monitoring.services import provision_service_account
from .models import BackupInvitation


class BackupInvitationAdminForm(forms.ModelForm):
    service_username = forms.CharField(
        label='Konto techniczne', max_length=57,
        help_text='Podaj nazwę istniejącego konta technicznego lub nowego konta bez hasła.',
    )

    class Meta:
        model = BackupInvitation
        fields = ('user', 'is_used')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.token_link:
            self.fields['service_username'].initial = Token.objects.filter(
                key=self.instance.token_link,
            ).values_list('user__username', flat=True).first()
        else:
            self.fields['user'].initial = User.objects.filter(
                username='maciek',
            ).values_list('pk', flat=True).first()

    def clean(self):
        cleaned = super().clean()
        owner = cleaned.get('user')
        username = cleaned.get('service_username', '').strip()
        if not owner or not username:
            return cleaned
        account = User.objects.filter(username=username).first()
        if account and (account.is_staff or account.is_superuser or account.has_usable_password()):
            self.add_error('service_username', 'Wybierz dedykowane konto techniczne bez hasła.')
            return cleaned
        if account and not account.is_active:
            self.add_error('service_username', 'Konto techniczne jest wyłączone.')
            return cleaned
        if self.instance.pk and self.instance.token_link:
            old_username = Token.objects.filter(key=self.instance.token_link).values_list(
                'user__username', flat=True,
            ).first()
            if old_username != username:
                self.add_error('service_username', 'Konta technicznego istniejącego zaproszenia nie można zmienić.')
        service = MonitoredService.objects.filter(service_user=account).first() if account else None
        if account:
            if service and service.owner_id != owner.pk:
                self.add_error('service_username', 'To konto techniczne ma już innego opiekuna w monitoringu.')
            if Token.objects.filter(user=account, key__in=BackupInvitation.objects.exclude(
                pk=self.instance.pk,
            ).values('token_link')).exists():
                self.add_error('service_username', 'To konto techniczne ma już inne zaproszenie.')
        if not service:
            service_name = f'backup_{username}'
            if MonitoredService.objects.filter(owner=owner, service_name=service_name).exclude(
                service_user=account,
            ).exists():
                self.add_error('service_username', 'Usługa o tej nazwie ma już inne konto techniczne.')
        cleaned['service_username'] = username
        return cleaned


@admin.register(BackupInvitation)
class BackupInvitationAdmin(admin.ModelAdmin):
    form = BackupInvitationAdminForm
    fields = ('user', 'service_username', 'token_link', 'is_used', 'created_at')
    list_display = ('user', 'technical_account', 'is_used', 'created_at')
    list_filter = ('is_used', 'created_at')
    search_fields = ('user__username', 'token_link')
    readonly_fields = ('token_link', 'created_at')
    ordering = ('is_used', 'user__username')

    @admin.display(description='Konto techniczne')
    def technical_account(self, obj):
        return Token.objects.filter(key=obj.token_link).values_list('user__username', flat=True).first() or '—'

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        username = form.cleaned_data['service_username']
        existing = MonitoredService.objects.filter(service_user__username=username).first()
        service, token = provision_service_account(
            owner=obj.user,
            username=username,
            service_name=existing.service_name if existing else f'backup_{username}',
            description=f'Backup telefonu: {username}',
        )
        if not existing and service.timeout_ms != 3_600_000:
            service.timeout_ms = 3_600_000
            service.save(update_fields=['timeout_ms'])
        obj.token_link = token.key
        super().save_model(request, obj, form, change)

