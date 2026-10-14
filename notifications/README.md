# Kolejka i historia powiadomień

Nowe zdarzenia Librusa (oceny, wiadomości, ogłoszenia) i zmiany statusu usług
Monitoringu zapisują powiadomienia do bazy. Nie wysyłają bezpośrednio e-maila
ani push. Jedna komenda Django obsługuje oba kanały; cron uruchamia ją co 5 minut.
Nie wymaga dodatkowych usług ani nowych zmiennych środowiskowych.

## Wybór kanału i historia

Dla każdego aktywnego członka grupy `Librus` lub `Monitoring` wybierany jest kanał:

- Aplikacja: aktywnie zarejestrowany telefon i uprawnienia do właściwego widoku.
  Przy kilku telefonach powstaje osobny wpis dla każdego.
- E-mail: użytkownik nie ma takiej rejestracji lub FCM jest wyłączone.
  Brak adresu także zostawia wpis w historii, jako `skipped` / `MissingEmail`.

Sam superuser bez odpowiedniej grupy nie jest odbiorcą powiadomień grupowych.
Rejestracja telefonu nie potwierdza, że jest w tej chwili online. Kanał i treść
są zapisywane podczas zdarzenia i pozostają takie same podczas ponowień.
Wiadomość e-mail przechowuje kopię tematu, treści i adresu z chwili zdarzenia;
push zawiera wyłącznie ogólną informację, bez treści wiadomości i danych dzieci.

Zdarzenie i wpisy kolejki zapisywane są atomowo wraz z oznaczeniem zdarzenia
jako przetworzonego. Powtórna synchronizacja nie tworzy duplikatów. Historyczne
flagi `wyslane`, `sent_at`, `powiadomienie_status` i `powiadomiono_at` w modelach
Librusa oznaczają teraz przetworzenie do kolejki, nie dostawę. Rzeczywisty wynik
i czas przyjęcia przez usługę są w historii powiadomień.

Dotychczasowe rekordy push zostają zachowane po migracji. Stare wysłane e-maile,
których treści nie zapisano w kolejce, nie są odtwarzane ani ponownie wysyłane.
Historia nowych powiadomień nie jest automatycznie usuwana i pozostaje po
usunięciu użytkownika/telefonu. Ze względu na kopie treści i adresów dostęp do
tej sekcji admina powinny mieć tylko osoby uprawnione.

## Komenda i cron co 5 minut

W aktywnym środowisku Python na serwerze:

```sh
python manage.py send_notifications --settings=mysite.settings.prod --limit 50
```

Komenda wysyła rzeczywiste powiadomienia. Domyślnie bierze do 50 wpisów
według czasu utworzenia, limit można ustawić od 1 do 500. Sam podgląd bez
wysyłki i zmian danych:

```sh
python manage.py send_notifications --settings=mysite.settings.prod --dry-run
```

Ścieżkę interpretera właściwego środowiska ustalisz po jego aktywowaniu:

```sh
python -c 'import sys; print(sys.executable)'
```

W cronie zastąp `/SCIEZKA_DO_PYTHONA` uzyskaną pełną ścieżką (nie używaj
systemowego `python`, który może nie mieć zależności projektu):

```cron
*/5 * * * * cd /home/host109829/mysite && /SCIEZKA_DO_PYTHONA manage.py send_notifications --settings=mysite.settings.prod --limit 50 >> logs/notifications-cron.log 2>&1
```

Utwórz `logs/` przed pierwszym uruchomieniem, jeśli nie istnieje. Usuń stary
wpis crona `send_push_notifications`, aby pozostał jeden harmonogram dla obu
kanałów. Stara komenda działa dla zgodności, ale obsługuje wyłącznie push.
Opcjonalnie nowa komenda pozwala wybrać `--channel email` lub `--channel push`.

Blokada pliku `logs/notifications.lock` uniemożliwia nakładanie się uruchomień
na tym samym serwerze i jest zwalniana przez system po zakończeniu procesu.
Dodatkowo każdy wpis jest atomowo zajmowany w bazie. Wysyłka sieciowa odbywa
się poza transakcją. Wpisy przerwane w stanie `processing` są odzyskiwane po
30 minutach. Po błędzie są najwyżej 3 próby: pierwsza i ponowienia po 5 oraz
15 minutach. Faktyczna próba następuje podczas kolejnego uruchomienia crona.
Po wyczerpaniu prób rekord zostaje jako `failed`.

Przed wysyłką są sprawdzane aktywność użytkownika i bieżące grupy; dla push
również aktywność telefonu, jego przypisanie i uprawnienia dashboardu.
Wylogowanie/przełączenie konta pomija oczekujące i zajęte wysyłki push tego
konta. Żądania już przyjętego przez usługę nie można wycofać; aplikacja Android
ponownie sprawdza konto i uprawnienia przed pokazaniem powiadomienia.

Błąd push nie uruchamia dodatkowego maila dla tego samego zdarzenia.
Unieważniony token Firebase dezaktywuje telefon; następne zdarzenia mogą
wybrać e-mail. Awaria jednego kanału nie blokuje pozostałych wpisów.
FCM wyłączone podczas obsługi kolejki pozostawia wpisy push w oczekiwaniu,
a e-maile nadal są obsługiwane. Awaria pomiędzy przyjęciem przez usługę a
zapisem wyniku w bazie może skutkować duplikatem po ponowieniu. Nie ma
potwierdzenia odczytania wiadomości przez użytkownika.

## Panel admina i diagnostyka

`/admin/` → Powiadomienia → **Powiadomienia**. Zachowano dotychczasowy adres
`/admin/notifications/pushdelivery/` i nazwę modelu `PushDelivery`, ale tabela
obsługuje już obydwa kanały. Filtry: kanał, grupa, stan. Dostępne są odbiorca,
treść, data utworzenia, liczba prób, termin następnej próby, czas przyjęcia
przez usługę i bezpieczny kod błędu.

- `pending` — oczekuje na cron/ponowienie.
- `processing` — zajęte przez proces wysyłający.
- `sent` — przyjęte przez Firebase lub backend poczty, nie potwierdzenie odczytu.
- `skipped` — np. cofnięte uprawnienia, usunięty odbiorca lub brak adresu.
- `failed` — błąd po wyczerpaniu prób albo nieważny token Firebase.

Akcja **Ponów zaznaczone powiadomienia z błędem** przywraca tylko `failed` do
kolejki i zeruje licznik prób. Nie wysyła natychmiast i nie odtwarza rekordów
`sent`. Usuwanie historii z tego panelu jest wyłączone.

Telefony: `/admin/notifications/pushdevice/`. Rejestracja wymaga nowego APK,
logowania, zgody na powiadomienia Androida oraz `FCM_ENABLED=1` na backendzie.
Bez tych warunków użytkownik dostaje e-mail. Brak sekcji admina oznacza brak
wdrożenia/restartu albo odpowiednich uprawnień do modeli.

`last_error_code` przechowuje wyłącznie nazwę wyjątku lub bezpieczny kod;
nie zapisuje kluczy i odpowiedzi z usług. `FileNotFoundError` zwykle oznacza
brak klucza, `PermissionError` brak dostępu, `JSONDecodeError` wadliwy JSON,
`UnregisteredError` nieaktualny token Firebase. Szczegóły działania crona
sprawdzaj w `logs/notifications-cron.log`.

## Konfiguracja Firebase i wdrożenie

Projekt: `domowy-dashboard`, Android: `com.macbilicki.domowydashboard`.
`android/app/google-services.json` i `.secrets/firebase-service-account.json`
są ignorowane przez Git; przenieś je osobno. Prywatnego klucza nie umieszczaj
w APK. Plik klucza powinien być czytelny tylko dla użytkownika Django.

```dotenv
FCM_ENABLED=1
FIREBASE_CREDENTIALS_FILE=.secrets/firebase-service-account.json
```

Ścieżka jest względna do katalogu projektu i taka sama jak domyślna.
Dawne `FCM_SEND_IMMEDIATELY` jest ignorowane: wysyłkę wykonuje tylko komenda.
Lokalne ustawienia `mysite.settings.dev` wyłączają FCM i używają konsolowego
backendu e-mail. Testy używają lokalnego backendu poczty i atrap Firebase.

Przed wdrożeniem zatrzymaj stary cron wysyłający i uruchomienia synchronizacji.
Po pobraniu kodu, w środowisku Python aplikacji:

```sh
python -m pip install -r requirements.txt
python manage.py migrate --plan --settings=mysite.settings.prod
python manage.py migrate --settings=mysite.settings.prod
```

Nowa migracja notifications `0002` rozszerza istniejącą tabelę i uzupełnia
klucze deduplikacji historycznych wpisów. Migracja librus `0011` zmienia opis
statusu powiadomień. Sprawdź także pozostałe zaległe migracje w planie.
Zrestartuj Django, przywróć synchronizacje i dodaj nowy cron. Nie trzeba
przebudowywać Androida dla tej zmiany. Te operacje na serwerze wykonuje
administrator; nie zostały wykonane podczas przygotowania kodu.

Wycofanie wymaga zatrzymania crona. Nie cofaj migracji po zapisaniu e-maili
do nowej kolejki: starszy schemat wymaga telefonu dla każdej wysyłki i nie
pomieści tych rekordów. Zmiana jest migracją do przodu; ewentualne cofnięcie
wymaga uzgodnionego zachowania danych albo odtworzenia kopii bazy.

## Weryfikacja lokalna

```sh
venv/bin/python manage.py check --settings=mysite.settings.dev
venv/bin/python manage.py test --settings=mysite.settings.dev
venv/bin/python manage.py makemigrations --check --dry-run --settings=mysite.settings.dev
```

Testy sprawdzają rejestrację i wylogowanie, oba kanały, odbiorców, deduplikację,
ponowienia, blokady crona, odzyskiwanie przerwanych wysyłek, cofnięcie uprawnień,
atomowy zapis zdarzenia i kolejki oraz migrację dotychczasowej historii push.
Nie wysyłają prawdziwej poczty ani powiadomień i używają tymczasowej bazy.
