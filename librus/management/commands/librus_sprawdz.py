import datetime
import logging
import os
from monitoring.client import MonitoringReporter
from django.core.management.base import BaseCommand
from django.utils import timezone
from librus.client import Librus
from librus.grades import sync_grades
from librus.models import Dziecko, Ocena, Wiadomosc, Ogloszenie
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ========================
#  Librus
# ========================

def parse_librus_date(value):
    if not value:
        return None

    for fmt in ("%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(value, fmt)
            return timezone.make_aware(dt)
        except Exception:
            continue
    return None

class Command(BaseCommand):
    help = 'Synchronizuje oceny, wiadomości i ogłoszenia Librusa; wywoływać co godzinę'

    def aktualizuj_oceny(self, librus_api, dziecko_obj):
        first_import = not Dziecko.objects.filter(
            pk=dziecko_obj.pk, oceny_pobrano_at__isnull=False,
        ).exists()
        outcome = sync_grades(dziecko_obj, librus_api.sprawdz_oceny())
        if first_import:
            # On a fresh deployment, historical grades are a baseline, not new
            # notifications. The migration separately marks pre-existing rows.
            Ocena.objects.filter(
                dziecko=dziecko_obj,
                powiadomienie_status=Ocena.POWIADOMIENIE_OCZEKUJE,
            ).update(powiadomienie_status=Ocena.POWIADOMIENIE_POMINIETE)
        errors = 0
        sent = 0
        for grade in dziecko_obj.oceny.select_related('dziecko__user').filter(
            archiwizowano_at__isnull=True,
            powiadomienie_status=Ocena.POWIADOMIENIE_OCZEKUJE,
        ):
            try:
                sent += int(grade.wyslij_powiadomienie())
            except Exception as exc:
                errors += 1
                logger.error('Nie udało się wysłać powiadomienia o nowej ocenie dla %s (%s).', dziecko_obj.user.username, type(exc).__name__)
        outcome['notifications_sent'] = sent
        if errors:
            raise RuntimeError(f'Nie wysłano powiadomień o {errors} nowych ocenach; ponowię przy następnym pobraniu.')
        return outcome
  
    # ========================
    #  Funkcje pomocnicze
    # ========================
 
    def aktualizuj_baze(self, librus_api, dziecko_obj):
        wiadomosci = librus_api.sprawdz_wiadomosci()
        for w in wiadomosci:

            juz_jest = Wiadomosc.objects.filter(
                wiadomosc_id=w["id"], 
                dziecko=dziecko_obj
            ).exists()

            if not juz_jest:
                tresc = librus_api.pobierz_tresc(w["id"])
                if tresc:
                    data_obj = parse_librus_date(w.get("data"))

                    if not data_obj:
                        logger.warning(
                            "Nieprawidłowa data w wiadomości %s: %s",
                            w.get("id"),
                            w.get("data")
                        )

                    logger.debug("DATA: %s", data_obj)
                    nowa_wiadomosc = Wiadomosc.objects.create(
                        wiadomosc_id=w["id"],
                        dziecko=dziecko_obj,
                        librus_data=data_obj, # Tu pamiętaj o poprawnym formacie daty!
                        nadawca = w["nadawca"].split(" (", 1)[0],
                        temat=w["temat"],
                        tresc=tresc
                    )
                    
                    # Od razu próbujemy wysłać powiadomienie
                    nowa_wiadomosc.wyslij_powiadomienie()


    def aktualizuj_ogloszenia_old(self, librus_api, dziecko_obj):
        for o in librus_api.sprawdz_ogloszenia():
            juz_jest = Ogloszenie.objects.filter(
                ogloszenie_id=o["id"], 
                dziecko=dziecko_obj
            ).exists()
            if not juz_jest:
                data_obj = parse_librus_date(o.get("data"))

                if not data_obj:
                    logger.warning(
                        "Nieprawidłowa data w ogłoszeniu %s: %s",
                        o.get("id"),
                        o.get("data")
                    )

                logger.debug("DATA: %s", data_obj)
                nowe_ogloszenie = Ogloszenie.objects.create(
                    ogloszenie_id=o["id"],
                    dziecko=dziecko_obj,
                    librus_data=data_obj,
                    nadawca=o["kto"],
                    tytul=o["tytul"],
                    tresc=o['tresc']
                )    
                # Od razu próbujemy wysłać powiadomienie
                nowe_ogloszenie.wyslij_powiadomienie()
                
    def aktualizuj_ogloszenia(self, librus_api, dziecko_obj):
        for o in librus_api.sprawdz_ogloszenia():
            # Szukamy ogłoszenia o tym samym tytule, dacie i dla tego samego dziecka
            ogloszenie, created = Ogloszenie.objects.get_or_create(
                ogloszenie_id=o["id"], # lub kombinacja tytul/data
                dziecko=dziecko_obj,
                tresc=o["tresc"],
                defaults={
                    'nadawca': o["kto"],
                    'tytul': o["tytul"],
                    'librus_data': parse_librus_date(o.get("data"))
                }
            )

            if not created:
                # Jeśli ogłoszenie już istniało, to nic nie robimy
                logger.debug(f"Nic nie robimy ogłoszenie istnieje: {o['tytul']}")
            else:
                logger.info(f"Dodano nowe ogłoszenie: {o['tytul']}")
                ogloszenie.wyslij_powiadomienie()

    def handle(self, *args, **options):
        monit = MonitoringReporter()
        results = []
        logger.info("Rozpoczynam import danych...wiadomości")   
                    
        logger.info("--- START PROGRAMU ---")
        # Definiujemy listę dzieci i ich dane (pobierane z .env)
        konfiguracja_dzieci = [
            {
                "user": os.getenv("LIBRUS_USER_ASIA"),
                "pass": os.getenv("LIBRUS_PASS_ASIA"),
                "name": os.getenv("LIBRUS_IMIE_ASIA")
            },
            {
                "user": os.getenv("LIBRUS_USER_ZUZIA"),
                "pass": os.getenv("LIBRUS_PASS_ZUZIA"),
                "name": os.getenv("LIBRUS_IMIE_ZUZIA")
            }
        ]

        for dziecko in konfiguracja_dzieci:
            result = {'dziecko': dziecko['name'], 'status_code': 0}
            results.append(result)
            # Sprawdzamy, czy dane w .env w ogóle istnieją
            if not dziecko["user"] or not dziecko["pass"]:
                logger.warning(f"Brak danych logowania dla: {dziecko['name']}. Pomijam.")
                result.update(status_code=2, error='Brak danych logowania do Librusa')
                continue

            librus_client = None
            try:
                logger.info(f"Rozpoczynam synchronizację dla: {dziecko['name']}")
                dziecko_obj = Dziecko.objects.filter(
                    user__username__iexact=dziecko["name"]
                ).first()
                if not dziecko_obj:
                    raise ValueError(f"Nie znaleziono dziecka w DB: {dziecko['name']}")
                librus_client = Librus(dziecko["user"], dziecko["pass"], dziecko["name"])
                logger.debug(dziecko_obj)
                # 2. Pobieranie danych (używamy Twoich funkcji pomocniczych)
                errors = []
                for name, operation in (
                    ('oceny', self.aktualizuj_oceny),
                    ('wiadomości', self.aktualizuj_baze),
                    ('ogłoszenia', self.aktualizuj_ogloszenia),
                ):
                    try:
                        outcome = operation(librus_client, dziecko_obj)
                        if name == 'oceny':
                            result['grades'] = outcome
                    except Exception as exc:
                        errors.append(f'{name}: {exc}')
                        result.update(status_code=2, error_type=type(exc).__name__)
                if errors:
                    result['error'] = '; '.join(errors)
                    logger.error('Błąd synchronizacji %s: %s', dziecko['name'], result['error'])
                else:
                    logger.info(f"Synchronizacja {dziecko['name']} zakończona sukcesem.")
            except Exception as e:
                logger.error(f"Błąd podczas obsługi dziecka {dziecko['name']}: {e}")
                result.update(status_code=2, error=str(e), error_type=type(e).__name__)
            finally:
                if librus_client:
                    try:
                        librus_client.close()
                        logger.debug(f"Sesja dla {dziecko['name']} zamknięta.")
                    except Exception as e:
                        result.update(status_code=2, error=str(e), error_type=type(e).__name__)
        failed = [result for result in results if result['status_code'] == 2]
        message = {'results': results, 'summary': 'Import Librusa zakończony' if not failed else 'Błędy importu Librusa'}
        if failed:
            message['error'] = '; '.join(f"{item['dziecko']}: {item['error']}" for item in failed)
        monit.send(status_code=2 if failed else 0, message=message)
        logger.info("--- KONIEC PROGRAMU ---")
        
