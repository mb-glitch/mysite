import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import bs4
import requests

from .session import CookieStore

logger = logging.getLogger(__name__)


class SessionRejected(RuntimeError):
    pass


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
        self.login_attempted = False
        self.store = CookieStore(user)
        try:
            self.session.cookies = self.store.load()
            if not self.session.cookies:
                self.do_login()
            else:
                self._get('https://synergia.librus.pl/wiadomosci/5')
        except Exception:
            self.session.close()
            self.store.close()
            raise

    def _request(self, method, url, **kwargs):
        try:
            response = getattr(self.session, method)(url, timeout=kwargs.pop('timeout', 20), **kwargs)
        except requests.Timeout:
            raise requests.Timeout('Przekroczono czas połączenia z Librusem.') from None
        except requests.RequestException:
            raise requests.ConnectionError('Nie udało się połączyć z Librusem.') from None
        return response

    @staticmethod
    def _check_status(response):
        if response.status_code >= 400:
            # Avoid putting OAuth redirect URLs / tokens in logs or monitoring.
            raise requests.HTTPError(f'Librus zwrócił HTTP {response.status_code}.')

    @staticmethod
    def _session_rejected(response):
        if response.status_code in (401, 403):
            return True
        url = urlparse(response.url)
        if url.hostname == 'api.librus.pl' or url.path.startswith('/loguj'):
            return True
        if 'json' in response.headers.get('Content-Type', '') or response.text.lstrip().startswith(('{', '[')):
            return False
        if 'Brak dostępu' in response.text:
            return True
        soup = bs4.BeautifulSoup(response.text, 'html.parser')
        return soup.select_one('input[type="password"]') is not None

    def _get(self, url, **kwargs):
        response = self._request('get', url, **kwargs)
        # Server errors and rate limits must not trigger password authentication.
        if response.status_code == 429 or response.status_code >= 500:
            self._check_status(response)
        if self._session_rejected(response):
            self.session.cookies.clear()
            self.store.save(self.session.cookies)
            self.do_login()
            response = self._request('get', url, **kwargs)
            if self._session_rejected(response):
                raise SessionRejected('Librus odrzucił sesję po ponownym logowaniu.')
        self._check_status(response)
        self.store.save(self.session.cookies)
        return response

    def do_login(self):
        if self.login_attempted:
            raise SessionRejected('Sesja Librusa wygasła; limit jednej próby logowania został wykorzystany.')
        self.login_attempted = True
        self.session.cookies.clear()
        logger.info('Logowanie do Librusa dla: %s', self.dziecko)

        # 1. START FLOW (WAŻNE)
        r1 = self._request('get',
            "https://synergia.librus.pl/loguj/portalRodzina",
            headers=self.session.headers,
            allow_redirects=True,
            timeout=20
        )
        self._check_status(r1)

        # 2. POST login
        login_url = r1.url
        parsed = urlparse(login_url)
        if parsed.scheme != 'https' or parsed.hostname not in ('api.librus.pl', 'synergia.librus.pl'):
            raise SessionRejected('Nieoczekiwany adres strony logowania Librusa.')

        r2 = self._request('post',
            login_url,
            headers=self.session.headers,
            data={
                "action": "login",
                "login": self.login,
                "pass": self.password
            },
            timeout=20
        )
        self._check_status(r2)

        # 3. JSON response check
        try:
            data = r2.json()
        except ValueError:
            raise SessionRejected('Nieprawidłowa odpowiedź logowania Librusa.') from None

        if not isinstance(data, dict) or not isinstance(data.get('goTo'), str):
            raise SessionRejected('Logowanie do Librusa zostało odrzucone.')

        # 4. FINALIZE SESSION
        final_url = urljoin('https://api.librus.pl', data['goTo'])
        parsed = urlparse(final_url)
        if parsed.scheme != 'https' or parsed.hostname != 'api.librus.pl':
            raise SessionRejected('Nieoczekiwane przekierowanie logowania Librusa.')
        self._check_status(self._request('get', final_url))

        # 5. TEST SESSION (KLUCZOWE)
        test = self._request('get',
            "https://synergia.librus.pl/wiadomosci/5",
            headers=self.session.headers
        )

        self._check_status(test)
        if self._session_rejected(test) or not self.session.cookies:
            raise SessionRejected('Librus nie potwierdził zalogowania.')
        soup = bs4.BeautifulSoup(test.text, 'html.parser')
        if not soup.select_one('a[href*="wyloguj"], a[href*="logout"], a[href*="/uczen/"], table.decorated'):
            raise SessionRejected('Nie rozpoznano strony zalogowanego użytkownika Librusa.')
        self.store.save(self.session.cookies)

        logger.info("Login OK")
    
    def close(self):
        try:
            self.session.close()
        finally:
            self.store.close()

    def _api_list(self, endpoint, key):
        response = self._get('https://synergia.librus.pl/gateway/api/2.0/' + endpoint)
        try:
            payload = response.json()
        except ValueError:
            raise ValueError('Librus zwrócił nieprawidłowy JSON ocen.') from None
        if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
            raise ValueError('Nie rozpoznano odpowiedzi API ocen Librusa.')
        # This endpoint currently returns the whole collection. Fail closed if
        # its contract changes to pagination, rather than archiving unseen pages.
        if set(payload) - {key, 'Resources', 'Url'}:
            raise ValueError('API ocen zwróciło stronicowaną odpowiedź; pominięto zapis.')
        return payload[key]

    def sprawdz_oceny(self):
        grades = self._api_list('Grades', 'Grades')
        if not grades:
            return []
        subjects = {str(item['Id']): item['Name'] for item in self._api_list('Subjects', 'Subjects')}
        categories = {str(item['Id']): item for item in self._api_list('Grades/Categories', 'Categories')}
        comments = {str(item['Id']): item['Text'] for item in self._api_list('Grades/Comments', 'Comments')}
        result = []
        for grade in grades:
            try:
                category = categories[str(grade['Category']['Id'])]
                description = [category['Name']]
                raw_weight = category.get('Weight')
                weight = None if raw_weight is None else Decimal(str(raw_weight))
                if weight is not None and (not weight.is_finite() or weight < 0):
                    raise ValueError
                for flag, label in (
                    ('IsSemester', 'Ocena śródroczna'),
                    ('IsSemesterProposition', 'Proponowana ocena śródroczna'),
                    ('IsFinal', 'Ocena roczna'),
                    ('IsFinalProposition', 'Proponowana ocena roczna'),
                ):
                    if grade.get(flag):
                        description.append(label)
                for comment in grade.get('Comments') or []:
                    description.append(comments[str(comment['Id'])])
                result.append({
                    'librus_id': str(grade['Id']),
                    'wartosc': grade['Grade'],
                    'przedmiot': subjects[str(grade['Subject']['Id'])],
                    'data': date.fromisoformat(grade['Date']),
                    'opis': '\n'.join(part for part in description if part),
                    'waga': weight,
                    'liczona_do_sredniej': grade['IsConstituent'],
                })
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise ValueError('Niepełne dane oceny, przedmiotu lub opisu; pominięto zapis ocen.') from None
        return result
    
    def sprawdz_wiadomosci(self):
        try:
            resp = self._get("https://synergia.librus.pl/wiadomosci/5", timeout=50)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Pobieranie listy wiadomości nie powiodło się: %s", e)
            raise

        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("table.decorated.stretch tr[class^='line']")
        lista = []
        for row in rows:
            tds = row.find_all("td")
            if len(tds) >= 5:
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
            resp = self._get("https://synergia.librus.pl/ogloszenia", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Pobieranie ogłoszeń nie powiodło się: %s", e)
            raise

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
            resp = self._get(f"https://synergia.librus.pl{id_}", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Pobieranie wiadomości %s nie powiodło się: %s", id_, e)
            raise
        soup = bs4.BeautifulSoup(resp.text, "html.parser")
        content = soup.find("div", {"class": "container-message-content"})
        return content.get_text("\n", strip=True) if content else None
