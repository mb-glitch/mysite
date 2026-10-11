# ==============================================================================
# monitoring/models.py
# Aplikacja: MONITORING
# Architektura: Service Accounts (Konta techniczne DRF + Nadzorca człowiek)
# ==============================================================================
from django.db import models
from django.contrib.auth.models import User, Group
from django.utils import timezone
from django.core.mail import send_mail
from django.core.validators import MinValueValidator
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
        4: "ERROR",
    }
    INTERVAL_CHOICES = (
        (60_000, "Co minutę"),
        (3_600_000, "Co godzinę"),
        (86_400_000, "Raz na dobę"),
    )

    # Człowiek (opiekun / użytkownik nadrzędny, domyślnie maciek)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        default=get_default_owner,
        related_name='supervised_services',
        verbose_name="Opiekun (użytkownik nadrzędny)"
    )

    # Konto techniczne maszyny w DRF (null=True, blank=True pozwala na bezpieczne migracje istniejących rekordów)
    service_user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
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
    timeout_ms = models.IntegerField(
        default=60_000, validators=[MinValueValidator(1)],
        help_text="Oczekiwany odstęp między raportami w milisekundach",
    )
    last_check_at = models.DateTimeField(null=True, blank=True, verbose_name="Ostatnie sprawdzenie")
    last_change_at = models.DateTimeField(null=True, blank=True, verbose_name="Ostatnia zmiana statusu")
    last_status = models.IntegerField(null=True, blank=True, verbose_name="Ostatni kod statusu")

    class Meta:
        verbose_name = "Usługa monitoringu (Service Account)"
        verbose_name_plural = "Usługi monitoringu (Service Accounts)"
        unique_together = ('owner', 'service_name')

    def __str__(self):
        owner_name = getattr(self.owner, 'username', 'brak')
        svc_name = getattr(self.service_user, 'username', 'tworzenie...')
        return f"{self.service_name} [{svc_name}] -> nadzorca: {owner_name}"

    @property
    def token(self):
        """Zwraca oficjalny token DRF przypisany do konta technicznego tego urządzenia."""
        if not self.service_user:
            return ""
        token_obj = Token.objects.filter(user=self.service_user).first()
        return token_obj.key if token_obj else ''

    def _get_timeout_seconds(self):
        return self.timeout_ms / 1000.0

    @property
    def interval_display(self):
        return dict(self.INTERVAL_CHOICES).get(self.timeout_ms, f"{self.timeout_ms} ms")

    def _get_monitoring_status(self):
        if not self.is_active or (self.service_user and not self.service_user.is_active):
            return 3
        latest_log = self.logs.order_by('-checked_at', '-pk').first()
        if not latest_log:
            return 2
        now = timezone.now()
        delay = (now - latest_log.checked_at).total_seconds()

        interval = self._get_timeout_seconds()
        # Brak raportów przez ponad trzy interwały ma pierwszeństwo przed
        # wynikiem ostatniego wykonania. Nie wydłużamy interwału w weekendy.
        if interval <= 0 or delay > interval * 3:
            return 2
        # Zgłoszony błąd nie może zmienić się w łagodniejszy PREFAIL z upływem czasu.
        if latest_log.status_code == 2:
            return 4
        if delay > interval or latest_log.status_code == 1:
            return 1
        return 0

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
                f"Konto techniczne: {getattr(self.service_user, 'username', 'brak')}\n"
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

