from django.db import models
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User
import logging

class Dziecko(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    librus_login = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name_plural = "Dzieci"
        
    def __str__(self):
        return self.user.get_full_name()
            

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
        return
        """Przygotowuje treść i wysyła e-mail."""
        pelna_tresc = (
            f"LIBRUS WIADOMOŚĆ ({self.dziecko})\n"
            f"Od: {self.nadawca}\n"
            f"Temat: {self.temat}\n"
            f"Data: {self.librus_data}\n\n"
            f"{self.tresc}"
        )
        subject = f"[{self.dziecko}] Nowa wiadomość: {self.temat}"
        recipient_list = ['adres@odbiorcy.pl'] # Możesz to trzymać w modelu Dziecko

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
                logging.info(f"Wysłano: {self.temat} ({self.dziecko})")
                return True
        except Exception as e:
            logging.error(f"Błąd wysyłki e-maila: {e}")
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
        unique_together = (('ogloszenie_id', 'dziecko'),)
        verbose_name_plural = "Ogłoszenia"
        
    def wyslij_powiadomienie(self):
        return
