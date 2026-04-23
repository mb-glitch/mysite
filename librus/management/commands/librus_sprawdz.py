import datetime
import logging
import requests
import bs4
import os
from pathlib import Path
from django.core.management.base import BaseCommand
from django.utils import timezone
from librus.models import Dziecko, Wiadomosc, Ogloszenie
from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Pobiera ścieżkę do folderu, w którym znajduje się ten konkretny plik .py
CURRENT_DIR = Path(__file__).resolve().parent
ENV_PATH = CURRENT_DIR / ".env.librus"

load_dotenv(dotenv_path=ENV_PATH)
logger = logging.getLogger('librus') # musi być zgodne z nazwą w settings.py

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

class Librus:
    def __init__(self, user, user_pass, dziecko):
        self.session = requests.Session()
        # Kluczowe: Przeglądarka zawsze wysyła User-Agent
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest' # To mówi Librusowi, że jesteśmy "nowoczesną aplikacją"
        })
        self.login = user
        self.dziecko = dziecko
        self.password = user_pass
        self.do_login()

    def do_login(self):
        logger.info("Logowanie: %s", self.login)

        # 1. START FLOW (WAŻNE)
        r1 = self.session.get(
            "https://synergia.librus.pl/loguj/portalRodzina",
            headers=self.session.headers,
            allow_redirects=True,
            timeout=20
        )

        # 2. POST login
        login_url = r1.url

        r2 = self.session.post(
            login_url,
            headers=self.session.headers,
            data={
                "action": "login",
                "login": self.login,
                "pass": self.password
            },
            timeout=20
        )

        # 3. JSON response check
        try:
            data = r2.json()
        except Exception:
            logger.error("Brak JSON — login failed")
            raise

        if "goTo" not in data:
            logger.error("Brak goTo — login failed")
            raise Exception("Login failed")

        # 4. FINALIZE SESSION
        final_url = "https://api.librus.pl" + data["goTo"]
        self.session.get(final_url, headers=self.session.headers, timeout=20)

        # 5. TEST SESSION (KLUCZOWE)
        test = self.session.get(
            "https://synergia.librus.pl/wiadomosci/5",
            headers=self.session.headers
        )

        if "Brak dostępu" in test.text:
            raise Exception("Session failed")

        logger.info("Login OK")
    
    def close(self):
        # self.session.cookies.clear()
        self.session.close()
    
    def sprawdz_wiadomosci(self):
        try:
            resp = self.session.get("https://synergia.librus.pl/wiadomosci/5", timeout=50)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Pobieranie listy wiadomości nie powiodło się: %s", e)
            return []

        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table.decorated.stretch tr[class^='line']")
        lista = []
        for row in rows:
            tds = row.find_all("td")
            if len(tds) >= 3:
                link = tds[2].find("a")
                if link and link.get("href", "").startswith("/wiadomosci/1/5/"):
                    id_ = link["href"]
                    data = tds[4].get_text(strip=True)
                    temat = tds[3].get_text(strip=True)
                    nadawca = link.get_text(strip=True)
                    lista.append({"id": id_, "data": data, "nadawca": nadawca, "temat": temat, "dziecko": self.dziecko})
        return lista

    def sprawdz_ogloszenia(self):
        try:
            resp = self.session.get("https://synergia.librus.pl/ogloszenia", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Pobieranie ogłoszeń nie powiodło się: %s", e)
            return []

        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        lista = []
        for o in soup.select("table"):
            data_tag = o.find("tr", class_="line0")
            tytul_tag = o.find("thead")
            if not data_tag or not tytul_tag:
                continue
            data = data_tag.find("td").get_text(strip=True)
            tytul = tytul_tag.find("td").get_text(strip=True)
            kto = ""
            tresc = ""
            for line in o.find_all("tr"):
                th = line.find("th")
                if th:
                    label = th.get_text(strip=True)
                    if label == "Treść":
                        tresc = line.find("td").get_text("\n", strip=True)
                    elif label == "Dodał":
                        kto = line.find("td").get_text(strip=True)
            id_ = f"{data}-{tytul}"
            lista.append({"id": id_, "data": data, "tytul": tytul, "kto": kto, "tresc": tresc, "dziecko": self.dziecko})
        return lista

    def pobierz_tresc(self, id_):
        try:
            resp = self.session.get(f"https://synergia.librus.pl{id_}", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Pobieranie wiadomości %s nie powiodło się: %s", id_, e)
            return None
        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        content = soup.find("div", {"class": "container-message-content"})
        return content.get_text("\n", strip=True) if content else None




class Command(BaseCommand):
    help = 'Importuje dane ze strony librus synergia, wywoływać co godzinę'
  
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
                        nadawca=w["nadawca"],
                        temat=w["temat"],
                        tresc=tresc
                    )
                    
                    # Od razu próbujemy wysłać powiadomienie
                    nowa_wiadomosc.wyslij_powiadomienie()


    def aktualizuj_ogloszenia(self, librus_api, dziecko_obj):
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

    def handle(self, *args, **options):
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
            # Sprawdzamy, czy dane w .env w ogóle istnieją
            if not dziecko["user"] or not dziecko["pass"]:
                logger.warning(f"Brak danych logowania dla: {dziecko['name']}. Pomijam.")
                continue

            librus_client = None
            try:
                logger.info(f"Rozpoczynam synchronizację dla: {dziecko['name']}")
                # 1. Logowanie
                librus_client = Librus(dziecko["user"], dziecko["pass"], dziecko["name"])
                dziecko_obj = Dziecko.objects.filter(
                    user__username__iexact=dziecko["name"]
                ).first()
                if not dziecko_obj:
                    logger.error(f"Nie znaleziono dziecka w DB: {dziecko['name']}")
                    continue
                self.stdout.write(self.style.SUCCESS(dziecko_obj))
                # 2. Pobieranie danych (używamy Twoich funkcji pomocniczych)
                self.aktualizuj_baze(librus_client, dziecko_obj)
                self.aktualizuj_ogloszenia(librus_client, dziecko_obj)
                
                logger.info(f"Synchronizacja {dziecko['name']} zakończona sukcesem.")
            except Exception as e:
                logger.error(f"Błąd podczas obsługi dziecka {dziecko['name']}: {e}")
            finally:
                if librus_client:
                    librus_client.close()
                    logger.debug(f"Sesja dla {dziecko['name']} zamknięta.")
        logger.info("--- KONIEC PROGRAMU ---")
        
