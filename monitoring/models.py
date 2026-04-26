from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User,  Group
from django.core.mail import send_mail
import logging

logger = logging.getLogger(__name__)

class MonitoredService(models.Model):
    STATUS_LABELS = {
        0: "OK",
        1: "PREFAIL",
        2: "FAIL",
        3: "INACTIVE",
    }
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)    
    timeout_ms = models.IntegerField(default=5000)
    last_check_at = models.DateTimeField(null=True, blank=True)
    last_status = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "Usługi do monitorowania"
        
    def __str__(self):
        return self.user.username

    def _get_timeout_seconds(self):
        weekday = timezone.now().weekday()  
        # 0 = poniedziałek, 6 = niedziela
        base_timeout = self.timeout_ms / 1000.0
        # np. weekend wolniejszy monitoring
        if weekday in (5, 6):  # sobota, niedziela
            return base_timeout * 4
        return base_timeout
        
    def _get_monitoring_status(self):
        """
        Zwraca status na podstawie czasu ostatniego logu:
        Bierze pod uwage że w weekend może się rzadziej zgłaszać
        0 - OK (zgłoszenie w terminie)
        1 - Brak zgłoszenia w czasie (timeout)
        2 - Brak zgłoszenia w czasie 3x timeout
        3 - Usługa nieaktywna
        """
        # Szukamy tylko wpisów, gdzie status to OK (0)
        last_healthy_log = self.logs.filter(status_code=0).order_by('-checked_at').first()
        
        if not last_healthy_log:
            return 2  # Nigdy nie było poprawnego meldunku
        
        now = timezone.now()
        delay = (now - last_healthy_log.checked_at).total_seconds()

        if not self.is_active:
            return 3
        elif delay <= self._get_timeout_seconds():
            return 0
        elif delay <= (self._get_timeout_seconds() * 3):
            return 1
        else:
            return 2

    @property
    def status(self):
        return self._get_monitoring_status()

    @property
    def status_display(self):
        return self.STATUS_LABELS.get(self.status, "UNKNOWN")
    
    @property
    def last_status_display(self):
        return self.STATUS_LABELS.get(self.last_status, "UNKNOWN")
                
    @property
    def is_healthy(self):
        """Zwraca True tylko jeśli status jest 0 (OK)"""
        return self.status == 0
 
    def wyslij_powiadomienie(self):
        current_status = self._get_monitoring_status()
        current_status_display = self.STATUS_LABELS.get(current_status, "UNKNOWN")
        group = Group.objects.get(name="Monitoring")
        recipient_list = group.user_set.values_list('email', flat=True)
        recipient_list = list(recipient_list)
        """Przygotowuje treść i wysyła e-mail."""
        now = timezone.localtime(timezone.now())
        pelna_tresc = (
        f"Data wygenerowania emaila: {now:%Y-%m-%d %H:%M:%S}\n"
        f"Uwaga zmiana statusu usługi {self.user.username}\n"
        f"{self.last_status_display} --> {current_status_display}"
        )
        subject = f"MONITORING USŁUG ({self.user.username} - {current_status_display})"
        try:
            # send_mail zwraca liczbę wysłanych maili (1 jeśli sukces)
            success = send_mail(
                subject,
                pelna_tresc,
                None, # Użyje DEFAULT_FROM_EMAIL z ustawień
                recipient_list,
                fail_silently=False,
            )
            
            if success:
                
                self.last_status = current_status
                self.last_check_at = timezone.now()
                self.save(update_fields=["last_status", "last_check_at"])
                return True
        except Exception as e:
            logger.error(f"Błąd wysyłki e-maila: {e}")
            return False
        
        
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
