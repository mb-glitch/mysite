# Domowy dashboard — Android / Kotlin

Prosta natywna aplikacja (Android 8.0+) z pastelowymi kartami i przyciskami,
wektorową ikoną domku, zakładkami Librus / Monitoring oraz logowaniem hasłem Django lub kodem QR. Nie wymaga WebView ani
logowania hasłem do Librusa. Nie uruchamia kopii i synchronizacji — czyta dane Django.

## Uruchomienie

1. Otwórz katalog `android/` w Android Studio. Wybierz JDK 17 lub 21 dla Gradle
   i zainstaluj Android SDK Platform 35. Projekt zawiera Gradle Wrapper 8.11.1.
2. Zbuduj i uruchom moduł `app` na telefonie/emulatorze, lub wykonaj:

   ```sh
   ./gradlew :app:assembleDebug
   ```

   APK debug znajdziesz w `app/build/outputs/apk/debug/app-debug.apk`.
   Wersję do dystrybucji podpisz własnym kluczem przez Android Studio
   (Generate Signed Bundle / APK); klucza nie dodawaj do repozytorium.
3. Backend musi mieć wdrożone endpointy i panel opisane poniżej. Otwórz panel
   backupu przez HTTPS. Przy aktywnym zaproszeniu znajdziesz osobny kod
   „Logowanie do aplikacji Android”. Jeśli opiekun nie ma jeszcze tokenu DRF,
   użyj przycisku „Utwórz kod logowania”.
4. Przy pierwszym uruchomieniu aplikacji wybierz „Zeskanuj kod QR”, zezwól
   na użycie aparatu i zeskanuj kod logowania. Aplikacja pokaże nazwę serwera
   do potwierdzenia, a potem zapisze adres i token razem. Kod instalatora
   Termuxa jest innym kodem i nie służy do logowania do aplikacji.
5. Alternatywnie wybierz „Zaloguj się hasłem” i wpisz adres serwera HTTPS,
   nazwę użytkownika i hasło konta Django. Aplikacja automatycznie pobiera token;
   jego wartości nie można podejrzeć ani ręcznie edytować.
6. Dostępne zakładki wynikają z uprawnień pobranych z serwera:
   grupa `Librus` daje Librusa, grupa `Monitoring` daje Monitoring, obie grupy
   dają oba widoki. Superuser widzi oba. Konta techniczne nie mają dostępu
   do tych zakładek. Konto z samą grupą `Monitoring` otwiera od razu Monitoring.
7. „Wyloguj” wyświetla potwierdzenie. Po zaakceptowaniu aplikacja usuwa
   zapisany adres i token oraz klucz szyfrowania, czyści dane na ekranie
   i wraca do logowania hasłem/QR. Anulowanie zachowuje sesję. Opóźniona
   odpowiedź wcześniejszego żądania nie przywraca danych po wylogowaniu.

Aplikacja nie odczytuje automatycznie prywatnych plików Termuxa ani danych innych
aplikacji. Token jest pobierany przy logowaniu lub odczytywany z QR, bez pokazywania jego wartości. Nie jest zaszyty w APK ani repozytorium.
Adres i token są razem szyfrowane AES-GCM, klucz jest w Android Keystore.
Hasło służy wyłącznie do logowania i nie jest zapisywane.
Kopie danych aplikacji i zrzuty jej ekranu są wyłączone. Wylogowanie usuwa zapisane
połączenie lokalnie; nie unieważnia tokenu na serwerze ani sesji innych urządzeń.
Dane raportów są przechowywane tylko w pamięci; bez internetu ekran
pokazuje błąd i przycisk ponowienia. Żądania mają timeouty i nie są automatycznie
ponawiane ani przekierowywane. Wymagany jest HTTPS z poprawnym certyfikatem.

## Kody QR i układ Librusa

Panel zachowuje dotychczasowy link i QR instalatora. Dodatkowy QR logowania jest
wyświetlany dla zaproszeń nieużytych i niearchiwalnych. Zobaczyć go może opiekun
zaproszenia albo superuser. Nieaktywne i techniczne konta nie otrzymują QR
logowania jako użytkownik. Kod zawiera token opiekuna, a nie token telefonu;
uprawnienia nadal wynikają z grup `Librus` / `Monitoring` lub konta superusera.

Format kodu (JSON, przykład bez rzeczywistego tokenu):

```json
{
  "type": "mysite-dashboard",
  "version": 1,
  "server_url": "https://twoja-domena.pl",
  "token": "<40-znakowy-token-DRF-użytkownika>"
}
```

Skanowanie jest lokalne, przez bibliotekę ZXing Android Embedded; obraz nie jest
wysyłany do serwisu zewnętrznego. Błędny kod lub anulowanie skanowania zachowuje
obecne połączenie. Zmiana konta wymaga wylogowania; przy nowym logowaniu kod jest sprawdzany
przez API przed zapisaniem połączenia. Nie ma już osobnego tokenu backupu. Wymagany jest HTTPS.
Kod zawiera dane dostępowe; nie publikuj go ani nie przekazuj osobom bez uprawnień.

Widok Librusa pokazuje: stan synchronizacji, wspólną listę 10 najnowszych
wiadomości/ogłoszeń dla wszystkich dzieci (z imieniem dziecka, datą i rozwijaną
treścią), bieżące oceny każdego dziecka, a na końcu jego średnie. Kliknięcie
w dowolne miejsce kafelka rozwija lub zwija treść. Sąsiadujące wpisy o dokładnie
identycznej, niepustej treści są łączone w jeden kafelek, z zachowaniem imion dzieci,
tytułów i metadanych. Równe treści rozdzielone innym wpisem pozostają osobno.
Limit 10 dotyczy wpisów przed łączeniem; kafelków może być mniej. Kafelki oddziela
fioletowa linia o wysokości jednego fizycznego piksela, bez dodatkowych marginesów. Wpisy są sortowane
według rzeczywistego czasu, także gdy daty zawierają różne strefy czasowe. API nadal
zwraca aktywność osobno dla każdego dziecka; aplikacja łączy ją bez zmiany kontraktu.

## API v1

Widoki dashboardów i profilu obsługują `GET` (także `HEAD` i `OPTIONS`)
i zwracają JSON z `Cache-Control: no-store`. Autoryzacja:

```http
Authorization: Token <istniejący-token-DRF>
Accept: application/json
```

Nie dodano modeli ani migracji. Panel udostępnia sesyjny, chroniony CSRF endpoint
`POST /backupapp/invitations/<id>/mobile-token/`, który tworzy token DRF opiekuna
lub wykorzystuje już istniejący. Wymaga logowania opiekuna albo superusera oraz
aktywnego zaproszenia. Odczyt panelu i API nie tworzy tokenów, a ponowne użycie
przycisku nie zmienia istniejącego tokenu. Endpoint nie nadaje żadnych uprawnień.
Token techniczny synchronizacji Librusa nie jest tokenem do przeglądania danych dzieci.

| Endpoint | Uprawnienia i zakres |
| --- | --- |
| `POST /api/v1/auth/login/` | JSON `{username, password}` konta Django; odpowiedź `{token, name, permissions}`. Tylko aktywne konta z dostępem do co najmniej jednego widoku. Istniejący token jest wykorzystywany ponownie; w razie braku zostaje utworzony. Limit 5 żądań/minutę na adres klienta. |
| `GET /api/v1/auth/me/` | Uwierzytelniony profil `{name, permissions: {librus, monitoring}}`, bez tokenu. Zakładki są sprawdzane przy każdym odświeżeniu, a API niezależnie sprawdza uprawnienia przy pobraniu danych. |
| `GET /api/v1/librus/dashboard/` | Superuser lub użytkownik z grupy `Librus`; wszystkie dzieci, analogicznie do wspólnych powiadomień tej grupy. Konta techniczne i konta powiązane z usługami są odrzucane. |
| `GET /api/v1/monitoring/dashboard/` | Superuser lub użytkownik z grupy `Monitoring`; wszystkie niearchiwalne usługi, tak jak zakres powiadomień grupy Monitoring. Konta techniczne nie mają dostępu. |
| `GET /api/v1/backup/dashboard/` | Poprzedni endpoint zachowany dla zgodności: użytkownik widzi własne niearchiwalne usługi, superuser wszystkie, konto techniczne przypisaną usługę. Nowa aplikacja nie korzysta z tego endpointu. |

Monitoring pokazuje bieżący status każdej usługi oraz jej ostatni raport:
datę, kod wyniku i podsumowanie. API przekazuje tekst pól `summary` / `error`,
nazwy i kody nieudanych zadań backupu oraz wyniki synchronizacji dzieci.
Pola z tokenami, surowe logi i dowolne nieznane elementy payloadu nie są zwracane;
40-znakowe tokeny DRF w tekście podsumowania są maskowane. Brak rozpoznanych pól
jest opisany jako „Raport nie zawiera podsumowania”. Brak raportów to `null`.
Status monitoringu jest wyliczany przez istniejący mechanizm, także według wieku
raportu, i może różnić się od kodu ostatniego wykonania. Odczyt nie uruchamia alertów.

### Format Librusa

- `generated_at`: data odczytu, ISO 8601.
- `latest_report`: `null` lub `{checked_at, status_code}` ostatniej synchronizacji.
- `children`: lista `{id, name, grades_synced_at, grade_summary, recent_grades, activity}`.
- `grade_summary`: `{subjects: [{name, mean, count}], projected, excluded}`;
  średnie to napisy z przecinkiem, `projected` może być `null`. Obliczenia korzystają
  z tej samej funkcji co dashboard WWW i pomijają oceny archiwalne.
- `recent_grades`: maksymalnie 10 ocen `{date, subject, value, description}`.
- `activity`: maksymalnie 10 najnowszych wiadomości/ogłoszeń łącznie na dziecko,
  `{id, kind, date, sender, title, content}`. `sender`, `title`, `content` mogą być `null`.

### Format Monitoringu

- `generated_at`: data odczytu, ISO 8601.
- `summary`: `{total, healthy}` — liczba usług i liczba ze statusem OK.
- `services`: lista `{id, name, description, owner, status_code, status,
  last_check_at, last_result_code, latest_report}`. Daty i wynik ostatniego raportu mogą być `null`.
- `latest_report`: `null` lub `{checked_at, status_code, summary}` ostatniego raportu usługi.
- `status_code`: `0` OK, `1` PREFAIL, `2` FAIL, `3` INACTIVE, `4` ERROR.
  `last_result_code` jest kodem raportu, a nie wyliczonym statusem monitoringu.

Błędy: `401` brak/błędny token lub nieaktywne konto, `403` brak uprawnień,
`405` niedozwolona metoda, `429` limit żądań logowania; odpowiedź DRF zawiera `detail`. Puste zbiory to `200`
z pustą listą. Odświeżanie nie wykonuje zapisów, nie wysyła wiadomości ani nie
kontaktuje się z zewnętrznym Librusem.

## Weryfikacja i ograniczenia

Backend: testy w `mysite/test_mobile_api.py` sprawdzają autoryzację, rozdzielenie
kont, zakres usług, brak ujawniania tokenów/logów, średnie, limity aktywności,
metody HTTP i brak wysyłania powiadomień. Uruchomienie na tymczasowej bazie SQLite:

```sh
venv/bin/python manage.py check --settings=mysite.settings.dev
venv/bin/python manage.py test --settings=mysite.settings.dev
```

W środowisku przygotowania zmian pełne 114 testów Django przeszło, w tym testy
kodów QR (uprawnienia, brak tokenu w czytelnym HTML, CSRF, brak zapisu przy GET,
ponowne użycie tokenu i ukrywanie nieaktywnych zaproszeń), logowania hasłem,
uprawnień grup oraz ostatnich raportów Monitoringu. Kotlin skompilowano,
13 testów jednostkowych Androida przeszło, a Android Lint zakończył się bez błędów
(z ostrzeżeniami dotyczącymi m.in. stylu kodu i wersji bibliotek):

```sh
./gradlew :app:testDebugUnitTest :app:lintDebug
```

Przebudowanie APK i instalację po zmianach wykonuje użytkownik w Android Studio.
Nowego logowania, wylogowania i uprawnień interfejsu nie zweryfikowano jeszcze
na telefonie; konieczne jest też wdrożenie nowych endpointów backendu.
Wrapper pobrano z repozytorium Gradle, jego JAR sprawdzono oficjalną sumą SHA-256;
konfiguracja przypina również sumę dystrybucji Gradle.

Przed użyciem należy zbudować aplikację i sprawdzić na urządzeniu: skanowanie QR,
odmowę uprawnień aparatu, anulowanie skanowania, zapis/odczyt
sesji po ponownym uruchomieniu, widoki według grup, ostatnie raporty, rozwijanie wiadomości, obrót ekranu,
brak internetu, błędne hasło, brak uprawnień, anulowanie wylogowania oraz
potwierdzenie wylogowania podczas pobierania danych. Backend nie został
wdrożony na produkcję; nie zmieniono żadnych istniejących tokenów ani uprawnień.

Dokumentacja użytych mechanizmów: [zgodność AGP 8.9 / Gradle](https://developer.android.com/build/releases/agp-8-9-0-release-notes),
[Android Keystore](https://developer.android.com/privacy-and-security/keystore),
[ZXing Android Embedded](https://github.com/journeyapps/zxing-android-embedded).
