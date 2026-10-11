# Instrukcje projektu Django

## Zasady ogólne
- Odpowiadaj po polsku.
- Przed zmianą kodu analizuj powiązane pliki.
- Wprowadzaj możliwie małe zmiany.
- Nie modyfikuj plików poza projektem.
- Nie usuwaj danych bez mojej zgody.

## Django
- Korzystaj z istniejących modeli.
- Nie wykonuj migracji bez mojej zgody.
- Po zmianach uruchamiaj testy.

## Dokumenty prawne
- Korzystaj z katalogu dokumenty/.
- Podawaj podstawę prawną.
- Sprawdzaj daty obowiązywania przepisów.
- Weryfikuj oryginalne dokumenty.

## Bezpieczeństwo
- Nie ujawniaj haseł ani tokenów.
- Nie wysyłaj danych bez zgody.
- Nie zmieniaj produkcyjnej bazy bez zgody.

## Produkcyjna baza danych
- Serwer bazy danych znajduje się na zdalnym hostingu.
- Łącz się z serwerem przez SSH, używając skonfigurowanego aliasu `hostido`.
- MySQL jest dostępny na serwerze przez socket UNIX `/var/lib/mysql/mysql.sock`.
- Preferuj połączenie przez konfigurację Django w `settings.py`.
- Możesz wykonywać zapytania odczytowe w celu analizy danych.
- Nie wykonuj operacji zmieniających bazę danych bez wyraźnej zgody.
- Nie uruchamiaj migracji na serwerze produkcyjnym.
- Nie modyfikuj plików na serwerze.
- Nie pobieraj całej bazy danych bez mojej zgody.
- Nigdy nie wyświetlaj haseł ani danych uwierzytelniających.
- Przed analizą większej ilości danych ustal zakres zapytania.
