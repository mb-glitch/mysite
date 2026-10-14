# ==============================================================================
# backupapp/models.py
# Aplikacja: BACKUPAPP
# Architektura: Service Accounts (Konta techniczne DRF dla telefonów i maszyn)
# ==============================================================================
import secrets
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from monitoring.models import MonitoredService, is_dedicated_service_account
from monitoring.services import provision_service_account


def generate_app_token():
    """
    Wydmuszka zachowana wyłącznie dla starych migracji (0006_...).
    """
    return secrets.token_hex(20)


def get_backup_service_owner():
    owner = User.objects.filter(username='maciek').first()
    if owner is None:
        raise ValidationError('Brak użytkownika maciek — nie można ustawić opiekuna nowej usługi backupu.')
    return owner


@transaction.atomic
def provision_backup_service(*, app_user, username, service_name):
    existing = MonitoredService.objects.select_related('owner').filter(service_user__username=username).first()
    service, token = provision_service_account(
        owner=existing.owner if existing else get_backup_service_owner(),
        username=username,
        service_name=existing.service_name if existing else service_name,
        description=f'Backup telefonu użytkownika {app_user.username}',
    )
    if not existing and service.timeout_ms != 3_600_000:
        service.timeout_ms = 3_600_000
        service.save(update_fields=['timeout_ms'])
    return service, token


class BackupInvitation(models.Model):
    """
    Zaproszenie instalacyjne telefonu w aplikacji 'backupapp'.
    Automatycznie tworzy konto techniczne DRF (np. svc_maciek_backup)
    i rejestruje je w MonitoredService z domyślnym opiekunem maciek.
    Pole user wskazuje użytkownika aplikacji i wartość KTO w instalatorze.
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='backup_invitations',
        verbose_name="Użytkownik aplikacji (KTO)",
        help_text="Użytkownik telefonu i katalogu backupu. Opiekunem nowej usługi monitoringu jest maciek."
    )
    token_link = models.CharField(
        max_length=64,
        blank=True,
        unique=True,
        verbose_name="Token DRF konta maszynowego"
    )
    technical_user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        editable=False,
        related_name='backup_invitation',
        verbose_name='Dedykowane konto techniczne',
    )
    is_used = models.BooleanField(
        default=False,
        verbose_name="Czy użyty / skonfigurowany"
    )
    is_archived = models.BooleanField(
        default=False, verbose_name="W archiwum",
        help_text="Ukryj zaproszenie i wyłącz jego link instalacyjny.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zaproszenie do instalacji"
        verbose_name_plural = "Zaproszenia do instalacji"
        ordering = ['user__username']

    @transaction.atomic
    def save(self, *args, **kwargs):
        if not self.token_link:
            # 1. Automatyczne konto techniczne dla telefonu: svc_<username>_backup
            machine_username = f"svc_{self.user.username.lower()}_backup"
            _, token_obj = provision_backup_service(
                app_user=self.user,
                username=machine_username,
                service_name=f"backup_{self.user.username.lower()}",
            )
            self.token_link = token_obj.key

        token = Token.objects.select_related('user').filter(key=self.token_link).first()
        self.technical_user = token.user if token and is_dedicated_service_account(token.user) else None

        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        account = self.technical_user
        if account and is_dedicated_service_account(account):
            return account.delete()
        return super().delete(*args, **kwargs)

    def __str__(self):
        status = 'UŻYTY' if self.is_used else 'AKTYWNY (do instalacji)'
        return f"{self.user.username.lower()} -> svc_{self.user.username.lower()}_backup [{status}]"

