# ==============================================================================
# backupapp/admin.py
# Aplikacja: BACKUPAPP
# ==============================================================================
from django import forms
from django.contrib import admin
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework.authtoken.models import Token

from monitoring.models import MonitoredService, is_dedicated_service_account
from monitoring.services import provision_service_account
from mysite.admin_filters import ArchiveFilter
from .models import BackupInvitation


class BackupInvitationAdminForm(forms.ModelForm):
    service_username = forms.CharField(
        label='Konto techniczne', max_length=57,
        help_text='Podaj nazwę istniejącego konta technicznego lub nowego konta bez hasła.',
    )

    class Meta:
        model = BackupInvitation
        fields = ('user', 'is_used', 'is_archived')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.token_link:
            self.fields['service_username'].required = False
            old_account = User.objects.filter(auth_token__key=self.instance.token_link).first()
            if old_account and not (old_account.is_staff or old_account.is_superuser or old_account.has_usable_password()):
                self.fields['service_username'].initial = old_account.username
            else:
                self.fields['service_username'].help_text = (
                    'Stare zaproszenie wskazuje konto osobiste. Podaj nazwę nowego konta technicznego bez hasła.'
                )
        else:
            self.fields['user'].initial = User.objects.filter(
                username='maciek',
            ).values_list('pk', flat=True).first()

    def clean(self):
        cleaned = super().clean()
        owner = cleaned.get('user')
        username = cleaned.get('service_username', '').strip()
        if self.instance.pk and cleaned.get('is_archived'):
            old_username = Token.objects.filter(key=self.instance.token_link).values_list(
                'user__username', flat=True,
            ).first()
            if username and old_username and username != old_username:
                self.add_error('service_username', 'Podczas archiwizacji nie zmieniaj konta technicznego.')
            return cleaned
        if not owner or not username:
            if owner and not username:
                self.add_error('service_username', 'Podaj konto techniczne.')
            return cleaned
        account = User.objects.filter(username=username).first()
        if account and (account.is_staff or account.is_superuser or account.has_usable_password()):
            self.add_error('service_username', 'Wybierz dedykowane konto techniczne bez hasła.')
            return cleaned
        if account and not account.is_active:
            self.add_error('service_username', 'Konto techniczne jest wyłączone.')
            return cleaned
        if self.instance.pk and self.instance.token_link:
            old_account = User.objects.filter(auth_token__key=self.instance.token_link).first()
            if old_account and old_account.username != username and not (
                old_account.is_staff or old_account.is_superuser or old_account.has_usable_password()
            ):
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
    fields = ('user', 'service_username', 'token_link', 'is_used', 'is_archived', 'created_at')
    list_display = ('user', 'technical_account', 'is_used', 'is_archived', 'created_at')
    list_filter = (ArchiveFilter, 'is_used', 'created_at')
    search_fields = ('user__username', 'token_link')
    readonly_fields = ('token_link', 'created_at')
    ordering = ('is_used', 'user__username')

    @admin.display(description='Konto techniczne')
    def technical_account(self, obj):
        return Token.objects.filter(key=obj.token_link).values_list('user__username', flat=True).first() or '—'

    def get_deleted_objects(self, objs, request):
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        for invitation in objs:
            account = invitation.technical_user
            if account and is_dedicated_service_account(account):
                if not request.user.has_perm('auth.delete_user'):
                    permissions.add(User._meta.verbose_name)
                deleted.append(f'Konto techniczne: {account.username} (wraz z tokenem)')
                counts['Konta techniczne'] = counts.get('Konta techniczne', 0) + 1
                service = MonitoredService.objects.filter(service_user=account).first()
                if service:
                    if not request.user.has_perm('monitoring.delete_monitoredservice'):
                        permissions.add(MonitoredService._meta.verbose_name)
                    deleted.append(f'Usługa monitoringu: {service.service_name} (wraz z raportami)')
                    counts['Usługi monitoringu'] = counts.get('Usługi monitoringu', 0) + 1
        return deleted, counts, permissions, protected

    def delete_model(self, request, obj):
        obj.delete()

    def delete_queryset(self, request, queryset):
        for pk in list(queryset.values_list('pk', flat=True)):
            invitation = BackupInvitation.objects.filter(pk=pk).first()
            if invitation:
                invitation.delete()

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        if change and obj.is_archived:
            super().save_model(request, obj, form, change)
            return
        username = form.cleaned_data['service_username']
        old_token_link = obj.token_link
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
        if change and old_token_link != obj.token_link:
            obj.is_used = False
        super().save_model(request, obj, form, change)

