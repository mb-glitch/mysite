from django.db import models
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User, Group
import logging

logger = logging.getLogger(__name__)

class Dziecko(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    librus_login = models.CharField(max_length=100, unique=True)
    oceny_pobrano_at = models.DateTimeField(null=True, blank=True, editable=False)
    oceny_archiwum_do = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name_plural = "Dzieci"
        
    def __str__(self):
        return self.user.get_full_name()
    
    def nazwa_usera(self):
        return self.user.username
            

class Ocena(models.Model):
    POWIADOMIENIE_OCZEKUJE = 'oczekuje'
    POWIADOMIENIE_WYSLANE = 'wyslane'
    POWIADOMIENIE_POMINIETE = 'pominiete'
    POWIADOMIENIE_STATUSY = [
        (POWIADOMIENIE_OCZEKUJE, 'Oczekuje na wysyłkę'),
        (POWIADOMIENIE_WYSLANE, 'Wysłano e-mail'),
        (POWIADOMIENIE_POMINIETE, 'Istniała przed włączeniem powiadomień'),
    ]

    dziecko = models.ForeignKey(Dziecko, on_delete=models.CASCADE, related_name='oceny')
    librus_id = models.CharField(max_length=64)
    wartosc = models.CharField('ocena', max_length=32)
    przedmiot = models.CharField(max_length=255)
    data = models.DateField()
    opis = models.TextField(blank=True)
    waga = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=1)
    liczona_do_sredniej = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    zmieniono_at = models.DateTimeField(default=timezone.now)
    ostatnio_widziana_at = models.DateTimeField(default=timezone.now)
    archiwizowano_at = models.DateTimeField(null=True, blank=True)
    powiadomienie_status = models.CharField(
        max_length=10, choices=POWIADOMIENIE_STATUSY, default=POWIADOMIENIE_OCZEKUJE,
    )
    powiadomiono_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'ocena'
        verbose_name_plural = 'Oceny'
        ordering = ['-data', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['dziecko', 'librus_id'], name='unique_ocena_per_dziecko'),
        ]

    def __str__(self):
        return f'{self.dziecko}: {self.przedmiot} — {self.wartosc}'

    def wyslij_powiadomienie(self):
        if self.powiadomienie_status != self.POWIADOMIENIE_OCZEKUJE or self.archiwizowano_at:
            return False
        recipients = list(
            Group.objects.get(name='Librus').user_set
            .exclude(email='').values_list('email', flat=True).distinct()
        )
        if not recipients:
            raise RuntimeError('Brak adresatów powiadomień w grupie Librus.')
        subject = f'[{self.dziecko.user.username}] Nowa ocena: {self.przedmiot} — {self.wartosc}'
        body = (
            f'Dziecko: {self.dziecko.user.get_full_name() or self.dziecko.user.username}\n'
            f'Przedmiot: {self.przedmiot}\n'
            f'Ocena: {self.wartosc}\n'
            f'Data: {self.data:%Y-%m-%d}\n'
            f'Opis: {self.opis or "—"}\n'
        )
        if send_mail(subject, body, None, recipients, fail_silently=False) != 1:
            raise RuntimeError('Serwer poczty nie potwierdził wysłania powiadomienia.')
        self.powiadomienie_status = self.POWIADOMIENIE_WYSLANE
        self.powiadomiono_at = timezone.now()
        self.save(update_fields=['powiadomienie_status', 'powiadomiono_at'])
        from notifications.services import queue_group_push
        queue_group_push(group='Librus', event_key=f'librus-grade:{self.pk}',
                         title='Librus — nowa ocena', body='Nowa ocena jest dostępna w aplikacji.')
        return True


class HistoriaOceny(models.Model):
    ocena = models.ForeignKey(Ocena, on_delete=models.CASCADE, related_name='historia')
    wartosc = models.CharField('ocena', max_length=32)
    przedmiot = models.CharField(max_length=255)
    data = models.DateField()
    opis = models.TextField(blank=True)
    waga = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=1)
    liczona_do_sredniej = models.BooleanField(default=True)
    zapisano_at = models.DateTimeField(default=timezone.now)
    powod = models.CharField(max_length=16, choices=[
        ('zmiana', 'Zmiana oceny'),
        ('brak', 'Brak w kolejnym pobraniu'),
        ('powrot', 'Ponowne pojawienie się oceny'),
        ('semestr', 'Archiwizacja semestru'),
    ])

    class Meta:
        verbose_name = 'historia oceny'
        verbose_name_plural = 'Historia ocen'
        ordering = ['-zapisano_at', '-pk']


class Wiadomosc(models.Model):
    wiadomosc_id = models.CharField(max_length=200)
    dziecko = models.ForeignKey(Dziecko, on_delete=models.CASCADE, related_name='wiadomosci')
    librus_data = models.DateTimeField(null=True, blank=True)
    nadawca = models.CharField(max_length=200, null=True, blank=True)
    temat = models.CharField(max_length=255, null=True, blank=True)
    tresc = models.TextField(null=True, blank=True)
    wyslane = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['wiadomosc_id', 'dziecko'], name='unique_wiadomosc_per_dziecko')
        ]
        verbose_name_plural = "Wiadomości"

    def wyslij_powiadomienie(self):
        group = Group.objects.get(name="Librus")
        recipient_list = group.user_set.values_list('email', flat=True)
        recipient_list = list(recipient_list)
        """Przygotowuje treść i wysyła e-mail."""
        pelna_tresc = (
            f"LIBRUS WIADOMOŚĆ ({self.dziecko})\n"
            f"Od: {self.nadawca}\n"
            f"Temat: {self.temat}\n"
            f"Data: {self.librus_data}\n\n"
            f"{self.tresc}"
        )
        subject = f"[{self.dziecko.user.username}] Nowa wiadomość: {self.temat} od: {self.nadawca}"

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
                self.wyslane = True
                self.sent_at = timezone.now()
                self.save()
                from notifications.services import queue_group_push
                queue_group_push(group='Librus', event_key=f'librus-message:{self.pk}',
                                 title='Librus — nowa wiadomość', body='Nowa wiadomość jest dostępna w aplikacji.')
                logger.info(f"Wysłano: {self.temat} ({self.dziecko})")
                return True
        except Exception as e:
            logger.error(f"Błąd wysyłki e-maila: {e}")
            return False
       

class Ogloszenie(models.Model):
    ogloszenie_id = models.CharField(max_length=100)
    dziecko = models.ForeignKey(Dziecko, on_delete=models.CASCADE, related_name='ogloszenia', null=True)
    nadawca = models.CharField(max_length=200, null=True, blank=True)
    librus_data = models.DateTimeField(null=True, blank=True)
    tytul = models.CharField(max_length=255)
    tresc = models.TextField()
    wyslane = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        # unique_together = (('ogloszenie_id', 'dziecko'),)
        verbose_name_plural = "Ogłoszenia"
        
    def wyslij_powiadomienie(self):
        group = Group.objects.get(name="Librus")
        recipient_list = group.user_set.values_list('email', flat=True)
        recipient_list = list(recipient_list)
        """Przygotowuje treść i wysyła e-mail."""
        pelna_tresc = (
            f"LIBRUS OGŁOSZENIE ({self.dziecko})\n"
            f"Autor ogłoszenia: {self.nadawca}\n"
            f"Data: {self.librus_data}\n"
            f"Tytuł: {self.tytul}\n\n"
            f"{self.tresc}"
        )
        subject = f"[{self.dziecko.user.username}] Nowe ogłoszenie: {self.tytul} od: {self.nadawca}"

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
                self.wyslane = True
                self.sent_at = timezone.now()
                self.save()
                from notifications.services import queue_group_push
                queue_group_push(group='Librus', event_key=f'librus-announcement:{self.pk}',
                                 title='Librus — nowe ogłoszenie', body='Nowe ogłoszenie jest dostępne w aplikacji.')
                logger.info(f"Wysłano: {self.tytul} ({self.dziecko})")
                return True
        except Exception as e:
            logger.error(f"Błąd wysyłki e-maila: {e}")
            return False
