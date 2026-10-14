import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class PushDevice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_devices')
    token = models.TextField(max_length=4096)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'telefon z powiadomieniami'
        verbose_name_plural = 'Telefony z powiadomieniami'

    def __str__(self):
        return f'{self.user_id} / {self.pk}'


class PushDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(PushDevice, on_delete=models.CASCADE, related_name='deliveries')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='push_deliveries')
    event_key = models.CharField(max_length=191)
    group = models.CharField(max_length=16, choices=[('Librus', 'Librus'), ('Monitoring', 'Monitoring')])
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=255)
    state = models.CharField(max_length=8, default='pending', choices=[
        ('pending', 'Oczekuje'), ('sent', 'Przyjęte przez FCM'), ('skipped', 'Pominięte'), ('failed', 'Błąd'),
    ])
    attempts = models.PositiveSmallIntegerField(default=0)
    retry_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['device', 'event_key'], name='unique_push_event_per_device')]
        indexes = [models.Index(fields=['state', 'retry_at'], name='push_pending_retry_idx')]
        verbose_name = 'wysyłka push'
        verbose_name_plural = 'Wysyłki push'
