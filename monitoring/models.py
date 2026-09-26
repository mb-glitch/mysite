# ==============================================================================
# monitoring/models.py
# Aplikacja: MONITORING
# Architektura: Service Accounts (Konta techniczne DRF + Nadzorca człowiek)
# ==============================================================================
from django.db import models
from django.contrib.auth.models import User, Group
from django.utils import timezone
from django.core.mail import send_mail
from rest_framework.authtoken.models import Token
import logging

logger = logging.getLogger(__name__)


def get_default_owner():
    """
    Zwraca ID domyślnego opiekuna (użytkownika 'maciek' lub pierwszego superusera/usera).
    Niezbędne przy automatycznych migracjach Django.
    """
    user = User.objects.filter(username='maciek').first()
    if user:
        return user.id
    first_user = User.objects.order_by('id').first()
    return first_user.id if first_user else 1




class MonitoredService(models.Model):
    """
    Konto maszynowe (Service Account) w standardzie DRF.
    - service_user: Dedykowane konto techniczne Django User (np. svc_maciek_backup),
      które posiada własny, oficjalny Token w tabeli DRF authtoken_token.
    - owner: Użytkownik nadrzędny (człowiek, domyślnie 'maciek'), który nadzoruje usługę i otrzymuje alerty.
    """
    STATUS_LABELS = {
        0: "OK",
        1: "PREFAIL",
        2: "FAIL",
        3: "INACTIVE",
    }

    # Człowiek (opiekun / użytkownik nadrzędny, domyślnie maciek)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        default=get_default_owner,
        related_name='supervised_services',
        verbose_name="Opiekun (użytkownik nadrzędny)"
    )

    # Konto techniczne maszyny w DRF
    service_user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        default=get_default_owner,
        related_name='service_profile',
        verbose_name="Konto techniczne maszyny (DRF User)"
    )

    service_name = models.CharField(
        max_length=64,
        verbose_name="Identyfikator usługi (np. backup, temperatura, vps_cron)"
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Opis (np. Telefon Pixel Maćka)"
    )

    is_active = models.BooleanField(default=True, verbose_name="Czy usługa aktywna")
    timeout_ms = models.IntegerField(default=5000, help_text="Bazowy timeout w milisekundach")
    last_check_at = models.DateTimeField(null=True, blank=True, verbose_name="Ostatnie sprawdzenie")
    last_change_at = models.DateTimeField(null=True, blank=True, verbose_name="Ostatnia zmiana statusu")
    last_status = models.IntegerField(null=True, blank=True, verbose_name="Ostatni kod statusu")

    class Meta:
        verbose_name = "Usługa monitoringu (Service Account)"
        verbose_name_plural = "Usługi monitoringu (Service Accounts)"
        unique_together = ('user', 'service_name')

    def __str__(self):
        return f"{self.service_name} [{self.service_user.username}] -> nadzorca: {self.user.username}"

    @property
    def token(self):
        """Zwraca oficjalny token DRF przypisany do konta technicznego tego urządzenia."""
        token_obj, _ = Token.objects.get_or_create(user=self.service_user)
        return token_obj.key

    def _get_timeout_seconds(self):
        weekday = timezone.now().weekday()
        base_timeout = self.timeout_ms / 1000.0
        # W weekendy 4x dłuższy czas oczekiwania
        if weekday in (5, 6):
            return base_timeout * 4
        return base_timeout

    def _get_monitoring_status(self):
        last_healthy_log = self.logs.filter(status_code=0).order_by('-checked_at').first()
        if not last_healthy_log:
            return 2
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
        return self.status == 0

    def sprawdz_status_i_wyslij_powiadomienie(self):
        """Wysyła e-mail przy awarii do opiekuna oraz grupy Monitoring."""
        current_status = self._get_monitoring_status()
        self.last_check_at = timezone.now()
        self.save(update_fields=['last_check_at'])

        if current_status != self.last_status:
            current_status_display = self.STATUS_LABELS.get(current_status, "UNKNOWN")
            
            recipients = []
            if self.owner.email:
                recipients.append(self.owner.email)
            try:
                group = Group.objects.get(name="Monitoring")
                recipients.extend(list(group.user_set.exclude(email='').values_list('email', flat=True)))
            except Group.DoesNotExist:
                logger.warning("Grupa 'Monitoring' nie istnieje w bazie.")

            recipients = list(set(recipients))
            now = timezone.localtime(timezone.now())
            pelna_tresc = (
                f"Data: {now:%Y-%m-%d %H:%M:%S}\n"
                f"Usługa: {self.service_name}\n"
                f"Konto techniczne: {self.service_user.username}\n"
                f"Opiekun: {self.owner.username}\n"
                f"Opis: {self.description}\n"
                f"Zmiana statusu: {self.last_status_display} --> {current_status_display}\n"
            )
            subject = f"MONITORING ({self.owner.username} / {self.service_name} - {current_status_display})"
            
            try:
                if recipients:
                    send_mail(subject, pelna_tresc, None, recipients, fail_silently=False)
                self.last_status = current_status
                self.last_change_at = timezone.now()
                self.save(update_fields=["last_status", "last_change_at"])
                return True
            except Exception as e:
                logger.error(f"Błąd wysyłki e-mail: {e}")
                return False


class LogEntry(models.Model):
    """
    Historia pojedynczych meldunków przesyłanych przez skrypty i aplikacje.
    Pole 'message' (JSONField) zapisuje dowolne parametry z ciałka zapytania.
    """
    STATUS_CHOICES = [
        (0, "ok"),
        (1, "warning"),
        (2, "critical"),
        (100, "app_specific"),
    ]

    service = models.ForeignKey(MonitoredService, on_delete=models.CASCADE, related_name="logs")
    status_code = models.IntegerField(choices=STATUS_CHOICES, null=True, blank=True, default=0)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)
    message = models.JSONField(null=True, blank=True, default=dict)

    class Meta:
        ordering = ['-checked_at']
        verbose_name = "Wpis logu usługi"
        verbose_name_plural = "Wpisy logów usług"

    def __str__(self):
        local_date = timezone.localtime(self.checked_at)
        return f"{self.service.service_name} - Kod {self.status_code} - {local_date:%H:%M:%S}"

