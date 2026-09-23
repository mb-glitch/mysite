from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class BackupInvitation(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='backup_invitation')
    token_link = models.CharField(max_length=64, unique=True, verbose_name="Token autoryzacyjny")
    is_used = models.BooleanField(default=False, verbose_name="Czy użyty / skonfigurowany")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Status instalacji telefonu"
        verbose_name_plural = "Statusy instalacji telefonów"
        ordering = ['user__username']

    def __str__(self):
        status = 'UŻYTY' if self.is_used else 'DOSTĘPNY'
        return f"{self.user.username.lower()} [{status}]"


class BackupAgent(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='backup_agent')
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_status_code = models.IntegerField(default=0)
    log = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = "Agent backupu"
        verbose_name_plural = "Agenci backupu"

    def __str__(self):
        status_text = "OK" if self.last_status_code == 0 else f"BŁĄD ({self.last_status_code})"
        seen_str = self.last_seen.strftime('%Y-%m-%d %H:%M') if self.last_seen else "brak aktywności"
        return f"{self.user.username.lower()} - {status_text} (ostatnio: {seen_str})"
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.authtoken.models import Token


class BackupInvitation(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='backup_invitation')
    token_link = models.CharField(max_length=64, blank=True, unique=True, verbose_name="Token autoryzacyjny")
    is_used = models.BooleanField(default=False, verbose_name="Czy użyty / skonfigurowany")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Zaproszenie / instalacja telefonu"
        verbose_name_plural = "Zaproszenia / instalacje telefonów"
        ordering = ['user__username']

    def save(self, *args, **kwargs):
        # Automatycznie przypisz klucz Tokena DRF użytkownika, jeśli nie podano
        if not self.token_link:
            token, _ = Token.objects.get_or_create(user=self.user)
            self.token_link = token.key
        super().save(*args, **kwargs)

    def __str__(self):
        status = 'UŻYTY' if self.is_used else 'AKTYWNY (do instalacji)'
        return f"{self.user.username.lower()} [{status}]"


class BackupAgent(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='backup_agent')
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_status_code = models.IntegerField(default=0)
    log = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = "Agent backupu"
        verbose_name_plural = "Agenci backupu"

    def __str__(self):
        status_text = "OK" if self.last_status_code == 0 else f"BŁĄD ({self.last_status_code})"
        seen_str = self.last_seen.strftime('%Y-%m-%d %H:%M') if self.last_seen else "brak aktywności"
        return f"{self.user.username.lower()} - {status_text} (ostatnio: {seen_str})"

