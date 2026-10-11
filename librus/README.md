# Librus: sesje i oceny

Dotychczasowa komenda `python manage.py librus_sprawdz` synchronizuje teraz
także oceny. Harmonogram pozostaje ten sam: dane odświeżają się podczas
każdego uruchomienia komendy, nie przez powiadomienia push z Librusa.

## Wdrożenie

Po pobraniu kodu, w środowisku skonfigurowanym dla właściwej bazy:

```bash
python manage.py migrate librus
```

Migracja `0007_oceny` dodaje tabele ocen i historii oraz czas ostatniego
udanego pobrania ocen przy dziecku. Nie zmienia istniejących wiadomości
ani ogłoszeń. Widok ocen wymaga zastosowanej migracji.

Migracje `0008` i `0009` dodają wagi ocen, informację o wliczaniu oceny do średniej
oraz datę graniczną ręcznej archiwizacji. Po tych migracjach uruchom zwykłą
synchronizację, aby uzupełnić rzeczywiste wagi wcześniej pobranych ocen.

## Sesje

Ciasteczka są zapisywane osobno dla każdego loginu w `.librus_sessions/`
w katalogu projektu. Opcjonalna zmienna `LIBRUS_SESSION_DIR` pozwala wskazać
inny prywatny katalog. Katalog ma uprawnienia `0700`, pliki ciasteczek `0600`;
jest wykluczony z Gita. Trzeba uruchamiać synchronizację jako użytkownik,
który może zapisywać w tym katalogu. Plików sesji nie należy publikować,
udostępniać przez WWW ani przenosić do repozytorium.

Przy starcie klient odczytuje ciasteczka i sprawdza sesję. Po odrzuceniu
sesji (401/403, przekierowanie do logowania lub rozpoznana strona logowania)
wykonuje dotychczasowy proces logowania, zapisuje nowe ciasteczka i ponawia
odczyt. Limit wynosi jedną próbę logowania hasłem na klienta podczas danego
uruchomienia. Timeout, błąd sieci, HTTP 429 i 5xx zgłaszają błąd bez kolejnego
logowania. Nie przedłużamy ważności ciasteczek ustalonej przez serwer.

Blokada systemowa `flock` zapobiega równoległemu użyciu tego samego konta
przez procesy korzystające z tego katalogu. Sama obecność pliku `.lock`
nie oznacza blokady — system zwalnia ją po zakończeniu procesu.

## Oceny

Klient pobiera komplet ocen oraz słowniki przedmiotów, kategorii i komentarzy
z API Synergii. Opis zawiera kategorię i dostępne komentarze; rodzaj oceny
śródrocznej/rocznej lub proponowanej jest oznaczony w opisie.

- Identyfikator Librusa wraz z dzieckiem zapobiega duplikatom.
- Nowe oceny są dopisywane, a zmienione aktualizowane z zachowaniem poprzednich wartości.
- Wpis nieobecny w kolejnym poprawnym pełnym zestawie trafia do archiwum.
- Powracająca ocena jest przywracana do aktualnych.
- Niezmienione oceny nie tworzą kolejnych kopii historii.
- Niepełna/niepoprawna odpowiedź lub błąd pobrania nie zmienia ocen w bazie.
- Aktualizacja zestawu jednego dziecka jest jedną transakcją.

Oceny i archiwum są dostępne po zalogowaniu przez `/librus/` → dziecko →
Oceny oraz w panelu administratora. Widok pokazuje ostatnie 100 zmian,
a pełna historia pozostaje w bazie. Błąd ocen trafia do raportu monitoringu;
pozostałe części synchronizacji nadal są wykonywane.

## Średnie i archiwizacja semestru

Średnia przedmiotu uwzględnia wagi kategorii z Librusa i tylko aktualne
oceny liczone do średniej. `4+` ma wartość 4,25, a `5-` — 4,75. Oceny
opisowe, bez wpływu na średnią, bez wagi i z wagą zero są pomijane. Średnia końcowa
to orientacyjna średnia z niezaokrąglonych średnich przedmiotów; na ekranie
wyświetlane jest jedno miejsce po przecinku. Nie jest to prognoza oficjalnych
ocen końcowych szkoły.

Po zakończeniu semestru uruchom ręcznie:

```bash
python manage.py librus_archiwizuj_oceny --do 2026-01-31
```

Data jest włącznie. Bez `--dziecko` komenda działa dla wszystkich dzieci;
można ograniczyć ją np. przez `--dziecko asia`. Archiwizacja jest trwała:
oceny z tą datą lub starsze pozostają w archiwum przy późniejszych
synchronizacjach, nawet jeśli nadal są zwracane przez Librusa. Ponowne
uruchomienie z tą samą datą nie dubluje historii. Datę można przesunąć tylko
do przodu. Panel pokazuje ostatnią ręczną datę archiwizacji dla każdego dziecka.

Źródło informacji o formacie API: implementacja klienta
[librus-rs](https://github.com/FlakM/librus-rs/tree/main/src/structs).
Format ocen, przedmiotów, kategorii i komentarzy został dodatkowo sprawdzony
na rzeczywistych odpowiedziach kont używanych w tym projekcie.
