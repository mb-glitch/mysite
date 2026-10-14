# Domowy dashboard — Android / Kotlin

Prosta natywna aplikacja (Android 8.0+) z pastelowymi kartami i przyciskami,
wektorową ikoną domku, przełącznikiem Librus / Backup i logowaniem kodem QR. Nie wymaga WebView ani
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
5. Wpisywanie danych ręcznie nadal jest dostępne w ustawieniach: bazowy adres
   serwera, np. `https://twoja-domena.pl` (bez `/api/v1/`), oraz token DRF
   użytkownika, bez prefiksu `Token`.
6. Opcjonalnie podaj osobny token telefonu dla zakładki Backup. Puste pole
   oznacza użycie tego samego tokenu co w zakładce Librus. Możesz również
   skonfigurować wyłącznie token backupu.

Aplikacja nie odczytuje automatycznie prywatnych plików Termuxa ani danych innych
aplikacji. Token można zeskanować z kodu QR lub wkleić w ustawieniach. Nie jest zaszyty w APK ani repozytorium.
Adres i tokeny są razem szyfrowane AES-GCM, klucz jest w Android Keystore.
Kopie danych aplikacji i zrzuty jej ekranu są wyłączone. Wylogowanie usuwa zapisane
połączenie. Dane raportów są przechowywane tylko w pamięci; bez internetu ekran
pokazuje błąd i przycisk ponowienia. Żądania mają timeouty i nie są automatycznie
ponawiane ani przekierowywane. Wymagany jest HTTPS z poprawnym certyfikatem.

## Kody QR i układ Librusa

Panel zachowuje dotychczasowy link i QR instalatora. Dodatkowy QR logowania jest
wyświetlany dla zaproszeń nieużytych i niearchiwalnych. Zobaczyć go może opiekun
zaproszenia albo superuser. Nieaktywne i techniczne konta nie otrzymują QR
logowania jako użytkownik. Kod zawiera token opiekuna, a nie token telefonu;
uprawnienia do Librusa nadal wynikają z grupy `Librus` lub konta superusera.

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
obecne połączenie. Nowy kod po potwierdzeniu zastępuje całe zapisane połączenie,
w tym usuwa opcjonalny token backupu poprzedniego serwera. Wymagany jest HTTPS.
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

Oba endpointy obsługują `GET` (także `HEAD` i `OPTIONS`) i zwracają JSON
z `Cache-Control: no-store`. Autoryzacja:

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
| `GET /api/v1/librus/dashboard/` | Superuser lub użytkownik z grupy `Librus`; wszystkie dzieci, analogicznie do wspólnych powiadomień tej grupy. Konta techniczne i konta powiązane z usługami są odrzucane. |
| `GET /api/v1/backup/dashboard/` | Użytkownik: własne niearchiwalne usługi; superuser: wszystkie niearchiwalne usługi; token techniczny: tylko przypisana usługa. |

Zakładka Backup, tak jak istniejący dashboard WWW, podsumowuje usługi monitoringu
(nie filtruje po nazwie `backup`). Statusy są wyliczane przez istniejący mechanizm
monitoringu, również na podstawie wieku ostatniego raportu. API nie zwraca zaproszeń,
linków instalacyjnych, tokenów, surowych logów ani dowolnej treści raportu synchronizacji.

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

### Format backupu

- `generated_at`: data odczytu, ISO 8601.
- `summary`: `{total, healthy}` — liczba usług i liczba ze statusem OK.
- `services`: lista `{id, name, description, owner, status_code, status,
  last_check_at, last_result_code}`. Daty i wynik ostatniego raportu mogą być `null`.
- `status_code`: `0` OK, `1` PREFAIL, `2` FAIL, `3` INACTIVE, `4` ERROR.
  `last_result_code` jest kodem raportu, a nie wyliczonym statusem monitoringu.

Błędy: `401` brak/błędny token lub nieaktywne konto, `403` brak uprawnień,
`405` niedozwolona metoda; odpowiedź DRF zawiera `detail`. Puste zbiory to `200`
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

W środowisku przygotowania zmian pełne 107 testów Django przeszło, w tym testy
kodów QR (uprawnienia, brak tokenu w czytelnym HTML, CSRF, brak zapisu przy GET,
ponowne użycie tokenu i ukrywanie nieaktywnych zaproszeń). Kotlin skompilowano,
9 testów jednostkowych Androida przeszło, a Android Lint zakończył się bez błędów
(z ostrzeżeniami dotyczącymi m.in. stylu kodu i wersji bibliotek):

```sh
./gradlew :app:testDebugUnitTest :app:lintDebug
```

Przebudowanie APK i instalację po zmianach wykonuje użytkownik w Android Studio.
Nowego skanowania aparatem i wyglądu nie zweryfikowano jeszcze na telefonie.
Wrapper pobrano z repozytorium Gradle, jego JAR sprawdzono oficjalną sumą SHA-256;
konfiguracja przypina również sumę dystrybucji Gradle.

Przed użyciem należy zbudować aplikację i sprawdzić na urządzeniu: skanowanie QR,
odmowę uprawnień aparatu, anulowanie skanowania, zapis/odczyt
ustawień po ponownym uruchomieniu, oba widoki, rozwijanie wiadomości, obrót ekranu,
brak internetu, błędny token, brak uprawnień oraz wylogowanie. Backend nie został
wdrożony na produkcję; nie zmieniono żadnych istniejących tokenów ani uprawnień.

Dokumentacja użytych mechanizmów: [zgodność AGP 8.9 / Gradle](https://developer.android.com/build/releases/agp-8-9-0-release-notes),
[Android Keystore](https://developer.android.com/privacy-and-security/keystore),
[ZXing Android Embedded](https://github.com/journeyapps/zxing-android-embedded).
