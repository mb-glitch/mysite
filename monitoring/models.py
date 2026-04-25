from django.db import models
from django.contrib.auth.models import User


class MonitoredService(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)    
    url = models.URLField(blank=True)
    timeout_ms = models.IntegerField(default=5000)

    class Meta:
        verbose_name_plural = "Usługi do monitorowania"
        
    def __str__(self):
        return self.user.username
        
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
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    status_code = models.IntegerField(null=True, blank=True)
    response_time_ms = models.IntegerField(null=True, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    message = models.TextField(blank=True)
    

    class Meta:
        ordering = ['-checked_at']
        verbose_name_plural = "Wpisy usług"
        
    def __str__(self):
        return f"{self.service.name} - {self.status} - {self.checked_at:%Y-%m-%d %H:%M:%S}"
