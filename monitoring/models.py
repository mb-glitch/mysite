from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User


class MonitoredService(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)    
    timeout_ms = models.IntegerField(default=5000)

    class Meta:
        verbose_name_plural = "Usługi do monitorowania"
        
    def __str__(self):
        return self.user.username
        
class LogEntry(models.Model):
    STATUS_CHOICES = [
        (0, "ok"),  # (lub NULL) OK Usługa działa idealnie.
        (1, "warning"),  # Usługa działa, ale np. kończy się miejsce na dysku.
        (2, "critical"),  # Krytyczny błąd aplikacji.
        (100, "app_specyfic"),  # 100+ Możesz tam wpisywać kody błędów ze swojej aplikacji (np. 500 jeśli Twój skrypt Pythona wywalił wyjątek).
    ]

    service = models.ForeignKey(MonitoredService, on_delete=models.CASCADE, related_name="logs")
    status_code = models.IntegerField(choices=STATUS_CHOICES, null=True, blank=True, default=0)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    message = models.JSONField(null=True, blank=True, default=dict)
    

    class Meta:
        ordering = ['-checked_at']
        verbose_name_plural = "Wpisy usług"
        
    def __str__(self):
        local_date = timezone.localtime(self.checked_at)
        # Pobieramy czytelną nazwę z STATUS_CHOICES
        status_display = dict(self.STATUS_CHOICES).get(self.status_code, f"Error {self.status_code}")
        return f"{self.service} - {status_display} - {local_date:%H:%M:%S}"
