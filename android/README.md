# Domowy dashboard — Android / Kotlin

Prosta natywna aplikacja (Android 8.0+) z jasnym motywem systemowym, przełącznikiem
Librus / Backup, odświeżaniem i ustawieniami połączenia. Nie wymaga WebView ani
logowania hasłem do Librusa. Nie uruchamia kopii i synchronizacji — czyta dane Django.

## Uruchomienie

1. Otwórz katalog `android/` w Android Studio. Wybierz JDK 17 dla Gradle
   i zainstaluj Android SDK Platform 35. Projekt zawiera Gradle Wrapper 8.11.1.
2. Zbuduj i uruchom moduł `app` na telefonie/emulatorze, lub wykonaj:

   ```sh
   ./gradlew :app:assembleDebug
   ```

   APK debug znajdziesz w `app/build/outputs/apk/debug/app-debug.apk`.
   Wersję do dystrybucji podpisz własnym kluczem przez Android Studio
   (Generate Signed Bundle / APK); klucza nie dodawaj do repozytorium.
3. Backend musi mieć wdrożone endpointy opisane poniżej. W aplikacji podaj
   bazowy adres serwera, np. `https://twoja-domena.pl` (bez `/api/v1/`),
   oraz istniejący token DRF użytkownika, bez prefiksu `Token`.
4. Opcjonalnie podaj osobny token telefonu dla zakładki Backup. Puste pole
   oznacza użycie tego samego tokenu co w zakładce Librus. Możesz również
   skonfigurować wyłącznie token backupu.

Aplikacja nie odczytuje automatycznie prywatnych plików Termuxa ani danych innych
aplikacji. Token trzeba wkleić w ustawieniach. Nie jest zaszyty w APK ani repozytorium.
Adres i tokeny są razem szyfrowane AES-GCM, klucz jest w Android Keystore.
Kopie danych aplikacji i zrzuty jej ekranu są wyłączone. Wylogowanie usuwa zapisane
połączenie. Dane raportów są przechowywane tylko w pamięci; bez internetu ekran
pokazuje błąd i przycisk ponowienia. Żądania mają timeouty i nie są automatycznie
ponawiane ani przekierowywane. Wymagany jest HTTPS z poprawnym certyfikatem.

## API v1

Oba endpointy obsługują `GET` (także `HEAD` i `OPTIONS`) i zwracają JSON
z `Cache-Control: no-store`. Autoryzacja:

```http
Authorization: Token <istniejący-token-DRF>
Accept: application/json
```

Nie dodano modeli, migracji ani endpointu wydającego nowe tokeny. Token użytkownika
można pobrać z istniejącej administracji DRF, jeśli już został utworzony. Token
techniczny synchronizacji Librusa nie jest tokenem do przeglądania danych dzieci.

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
- `activity`: maksymalnie 10 najnowszych wiadomości/ogłoszeń łącznie,
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

W środowisku przygotowania zmian pełne 102 testy Django przeszły. Próba
`./gradlew :app:assembleDebug` zakończyła się przed kompilacją: brak Javy;
nie ma również Android SDK. APK nie zostało zbudowane ani przetestowane na telefonie.
Wrapper pobrano z repozytorium Gradle, jego JAR sprawdzono oficjalną sumą SHA-256;
konfiguracja przypina również sumę dystrybucji Gradle.

Przed użyciem należy zbudować aplikację i sprawdzić na urządzeniu: zapis/odczyt
ustawień po ponownym uruchomieniu, oba widoki, rozwijanie wiadomości, obrót ekranu,
brak internetu, błędny token, brak uprawnień oraz wylogowanie. Backend nie został
wdrożony na produkcję; nie zmieniono żadnych istniejących tokenów ani uprawnień.

Dokumentacja użytych mechanizmów: [zgodność AGP 8.9 / Gradle](https://developer.android.com/build/releases/agp-8-9-0-release-notes),
[Android Keystore](https://developer.android.com/privacy-and-security/keystore).
