# Powiadomienia push Firebase

Django wysyła ogólne powiadomienie push po udanym wysłaniu e-maila o ocenie,
wiadomości, ogłoszeniu Librusa lub zmianie statusu monitorowanej usługi.
Odbiorcami są aktywne telefony aktywnych użytkowników należących odpowiednio
do grup `Librus` i `Monitoring`, tak jak odbiorcy maili. Sam superuser bez grupy
nie jest odbiorcą powiadomień grupowych. Treści wiadomości i dane dzieci nie
są przekazywane do Firebase. Kliknięcie otwiera właściwy dashboard.

## Konfiguracja

Projekt Firebase: `domowy-dashboard`, aplikacja Android:
`com.macbilicki.domowydashboard`. Plik konfiguracji Androida umieść w
`android/app/google-services.json` (ignorowany przez Git). Zawiera konfigurację
klienta, nie zastępuje prywatnego klucza backendu. Bez niego APK buduje się
z wyłączoną integracją Firebase.

Klucz konta serwisowego pobierz z Firebase Console → Ustawienia projektu →
Konta usługi → Firebase Admin SDK → Wygeneruj nowy klucz prywatny.
Zapisz w `.secrets/firebase-service-account.json` w katalogu projektu;
`.secrets/` jest ignorowany przez Git. Skopiuj plik osobno na serwer i ogranicz
jego odczyt do użytkownika Django. Nie umieszczaj klucza w APK ani repozytorium.
W tym samym projekcie Firebase Cloud Messaging API musi być włączone.

W `.env` serwera:

```dotenv
FCM_ENABLED=1
FCM_SEND_IMMEDIATELY=1
FIREBASE_CREDENTIALS_FILE=.secrets/firebase-service-account.json
```

Ścieżka względna jest rozwiązywana względem katalogu projektu, nie katalogu
roboczego procesu. Domyślna ścieżka jest identyczna, więc jej wpis można pominąć.
`FCM_ENABLED` domyślnie wynosi `0`. Ustawienia `mysite.settings.dev` zawsze
wyłączają wysyłkę i rejestrację push; testy włączają ją wyłącznie z atrapami.

## Wdrożenie przez administratora

Kod przygotowano lokalnie; migrację `notifications/0001_initial.py` zastosowano
do lokalnej developerskiej bazy SQLite na polecenie użytkownika. Wdrożenie,
migracje produkcyjnej bazy oraz prawdziwą wysyłkę wykonuje administrator. Po pobraniu zmian na serwerze, w aktywnym
środowisku Python projektu:

```sh
python -m pip install -r requirements.txt
python manage.py migrate --settings=mysite.settings.prod
```

Sprawdź plan migracji przed ich wykonaniem, zwłaszcza gdy serwer ma inne
zaległe migracje. Nowa migracja `notifications/0001_initial.py` tworzy tabele
telefonów i kolejki. Po ustawieniu `.env` i skopiowaniu klucza zrestartuj Django
mechanizmem hostingu. Dotychczas wysłane e-maile nie są automatycznie odtwarzane
jako push. Przebuduj APK, zainstaluj, zaloguj się i udziel zgody na powiadomienia.
Zmiana applicationId oznacza osobną instalację względem dawnego
`pl.mysite.dashboard`; wymaga ponownego logowania.

Nowe powiadomienia są wysyłane po zatwierdzeniu transakcji. Błędy Firebase nie
cofają udanego e-maila. Próby ponawiane są maksymalnie trzykrotnie, po jednej
oraz dwóch minutach; do obsługi ponowień skonfiguruj cykliczne uruchamianie
(np. co minutę, w katalogu projektu, interpreterem jego środowiska):

```sh
python manage.py send_push_notifications --settings=mysite.settings.prod --limit 50
```

Ta komenda przy włączonym FCM wysyła rzeczywiste powiadomienia. Nie została
uruchomiona na produkcji podczas przygotowania integracji.

## Diagnostyka

Panel `/admin/` → Notifications:

- Telefony z powiadomieniami: `/admin/notifications/pushdevice/`.
  Po logowaniu i zgodzie na powiadomienia telefon powinien być aktywny,
  przypisany do właściwego użytkownika. Rejestracja wymaga włączonego FCM.
- Wysyłki push: `/admin/notifications/pushdelivery/`.
  `pending` — czeka na próbę, `sent` — przyjęte przez Firebase,
  `skipped` — brak bieżących uprawnień lub wylogowane/przepisane urządzenie,
  `failed` — wyczerpane próby lub unieważniony token Firebase.
  Sprawdź `attempts`, `retry_at` i `last_error_code`.

Brak sekcji oznacza brak wdrożenia/restartu albo uprawnień do modeli.
Brak telefonu: nowy APK, logowanie, zgoda Androida, konfiguracja JSON,
`FCM_ENABLED` i dostęp do API. Brak wysyłki przy obecnym telefonie: członkostwo
w odpowiedniej grupie oraz czy zdarzenie wystąpiło po rejestracji telefonu.

`last_error_code` przechowuje wyłącznie nazwę klasy wyjątku, nie sekret ani
odpowiedź Firebase. `FileNotFoundError` oznacza zwykle brak pliku klucza,
`PermissionError` brak dostępu, `UnregisteredError` nieaktualny token telefonu.
Pozostałe błędy uprawnień Firebase wymagają sprawdzenia projektu, konta
serwisowego i włączonego API. Log Django zawiera komunikaty „Push nie został
przyjęty przez FCM” oraz „Nie udało się przygotować push”.

Stan `sent` nie potwierdza wyświetlenia na telefonie. Android weryfikuje aktualne
konto i grupę przez backend przed pokazaniem powiadomienia; potrzebuje internetu.
Sprawdź też uprawnienia aplikacji i jej kanały Librus / Monitoring w ustawieniach
Androida. Wymuszona blokada aplikacji lub ograniczenia producenta mogą zatrzymać
odbiór. Nie zweryfikowano jeszcze rzeczywistej dostawy na urządzeniu.

## API i ochrona sesji

`PUT /api/v1/push/devices/<uuid>/` z tokenem DRF zalogowanego użytkownika
przyjmuje JSON `{"token": "<token-rejestracyjny-FCM>"}`. Zwraca tylko UUID
instalacji (201/200). Rejestracja bez uprawnień: 403; FCM wyłączone: 503;
token FCM już przypisany do innego UUID: 409. Token FCM nie jest tokenem logowania.
`DELETE` tego samego adresu dezaktywuje tylko urządzenie danego użytkownika
(204) i pomija jego oczekujące wysyłki. Zmiana konta pomija stare wysyłki.

Wylogowanie lokalnie usuwa sesję, oczekującą pracę i widoczne powiadomienia.
Wyrejestrowanie na serwerze i w Firebase wykonywane jest w miarę dostępności
sieci. Nawet jeśli się nie powiedzie, aplikacja nie wyświetla powiadomień starego
konta. Serwer ponownie sprawdza grupy przed wysyłką. Kolejka deduplikuje zdarzenia
na urządzenie; aplikacja deduplikuje pracę według UUID wysyłki.

## Sprawdzenia

```sh
venv/bin/python manage.py check --settings=mysite.settings.dev
venv/bin/python manage.py test --settings=mysite.settings.dev
venv/bin/python manage.py makemigrations --check --dry-run --settings=mysite.settings.dev
cd android
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

Testy Django używają tymczasowej bazy, lokalnego backendu e-mail oraz atrap
Firebase. Nie wysyłają prawdziwych powiadomień.

Źródła: [Firebase Admin SDK](https://firebase.google.com/docs/admin/setup),
[Android FCM i zgoda na powiadomienia](https://firebase.google.com/docs/cloud-messaging/android/get-started).
