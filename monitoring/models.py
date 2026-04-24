from django.db import models
import secrets

class MonitoredService(models.Model):
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    api_token = models.CharField(max_length=64, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.api_token:
            self.api_token = secrets.token_hex(32)
        super().save(*args, **kwargs)
        
    def __str__(self):
        return self.name
        
class LogEntry(models.Model):
    STATUS_CHOICES = [
        ("ok", "OK"),
        ("fail", "Fail"),
        ("timeout", "Timeout"),
    ]

    service = models.ForeignKey(
        MonitoredService,
        on_delete=models.CASCADE,
        related_name="logs"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    status_code = models.IntegerField(null=True, blank=True)
    response_time_ms = models.IntegerField(null=True, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)
    message = models.TextField(blank=True)

    def __str__(self):
        return f"{self.service.name} - {self.status} - {self.checked_at}"
