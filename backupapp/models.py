# ==============================================================================
# backupapp/models.py
# Aplikacja: BACKUPAPP
# Architektura: Service Accounts (Konta techniczne DRF dla telefonów i maszyn)
# ==============================================================================
import secrets
from django.db import models
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from monitoring.models import MonitoredService


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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zaproszenie do instalacji"
        verbose_name_plural = "Zaproszenia do instalacji"
        ordering = ['user__username']

    def save(self, *args, **kwargs):
        if not self.token_link:
            # 1. Automatyczne konto techniczne dla telefonu: svc_<username>_backup
            machine_username = f"svc_{self.user.username.lower()}_backup"
            machine_user, _ = User.objects.get_or_create(
                username=machine_username,
                defaults={
                    'is_active': True,
                    'first_name': 'Konto maszynowe',
                    'last_name': f'Backup ({self.user.username})'
                }
            )

            # 2. Oficjalny token DRF dla tego konta maszynowego:
            token_obj, _ = Token.objects.get_or_create(user=machine_user)
            self.token_link = token_obj.key

            # 3. Rejestracja w MonitoredService (opiekun: self.user, konto: machine_user):
            MonitoredService.objects.get_or_create(
                service_user=machine_user,
                defaults={
                    'owner': self.user,
                    'service_name': f"backup_{self.user.username.lower()}",
                    'description': f"Backup telefonu użytkownika {self.user.username}"
                }
            )

        super().save(*args, **kwargs)

    def __str__(self):
        status = 'UŻYTY' if self.is_used else 'AKTYWNY (do instalacji)'
        return f"{self.user.username.lower()} -> svc_{self.user.username.lower()}_backup [{status}]"

