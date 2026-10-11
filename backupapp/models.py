# ==============================================================================
# backupapp/models.py
# Aplikacja: BACKUPAPP
# Architektura: Service Accounts (Konta techniczne DRF dla telefonów i maszyn)
# ==============================================================================
import secrets
from django.db import models, transaction
from django.contrib.auth.models import User
from monitoring.services import provision_service_account


def generate_app_token():
    """
    Wydmuszka zachowana wyłącznie dla starych migracji (0006_...).
    """
    return secrets.token_hex(20)


class BackupInvitation(models.Model):
    """
    Zaproszenie instalacyjne telefonu w aplikacji 'backupapp'.
    Automatycznie tworzy konto techniczne DRF (np. svc_maciek_backup)
    i rejestruje je w MonitoredService ze wskazaniem opiekuna (człowieka).
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='backup_invitations',
        verbose_name="Opiekun (człowiek)"
    )
    token_link = models.CharField(
        max_length=64,
        blank=True,
        unique=True,
        verbose_name="Token DRF konta maszynowego"
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
            _, token_obj = provision_service_account(
                owner=self.user,
                username=machine_username,
                service_name=f"backup_{self.user.username.lower()}",
                description=f"Backup telefonu użytkownika {self.user.username}",
            )
            self.token_link = token_obj.key

        super().save(*args, **kwargs)

    def __str__(self):
        status = 'UŻYTY' if self.is_used else 'AKTYWNY (do instalacji)'
        return f"{self.user.username.lower()} -> svc_{self.user.username.lower()}_backup [{status}]"

