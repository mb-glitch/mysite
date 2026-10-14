# Monitoring usług

W panelu administratora pole „Oczekiwany interwał raportów” wybiera odstęp:
minuta (60 000 ms), godzina (3 600 000 ms) lub doba (86 400 000 ms).
Istniejące niestandardowe wartości pozostają dostępne dla danej usługi.
Nowe usługi domyślnie mają interwał jednej minuty; należy dobrać go do
harmonogramu konkretnej usługi. Zmiana interwału na serwerze nie zmienia
harmonogramu uruchamiania skryptu na urządzeniu.

Termin liczymy od odebrania ostatniego raportu, jednakowo we wszystkie dni:

| Status | Warunek |
| --- | --- |
| INACTIVE | Wyłączona usługa lub konto techniczne. |
| OK | Raport mieści się w interwale i nie zgłasza ostrzeżenia ani błędu. |
| PREFAIL | Upłynął ponad jeden, ale nie więcej niż trzy interwały, albo ostatni raport zgłosił ostrzeżenie. |
| FAIL | Upłynęły ponad trzy interwały lub usługa nigdy nie przesłała raportu. |
| ERROR | Ostatni raport zgłosił błąd; pozostaje ERROR do nowego raportu lub przekroczenia trzech interwałów (wtedy FAIL). |

`POST /api/monitoring/` wymaga nagłówka `Authorization: Token <token>`.
Puste zgłoszenie lub JSON bez `status_code` oznacza sukces. Pole `status_code`
w JSON zachowuje dotychczasowe znaczenie: `0` sukces, `1` ostrzeżenie,
`2` błąd, `100` dane aplikacji bez błędu. Przykładowy raport błędu:

```json
{"status_code": 2, "error": "Nie udało się wykonać kopii", "exit_code": 23}
```

Kody raportów różnią się od kodów stanu usługi: ERROR ma wewnętrzny kod `4`,
ale aplikacja nadal przesyła `status_code: 2`. Pozostałe dane JSON są zapisywane
w historii. Datą kontaktu jest czas odbioru przez serwer.

Dashboard oblicza stan przy odświeżeniu. Powiadomienia o braku raportów wymagają
regularnego uruchamiania istniejącej komendy, np. raz na minutę przez cron:

```sh
python manage.py monitoring_sprawdz
```

Komenda musi korzystać z tego samego środowiska, ustawień i bazy co serwer.
Powiadomienia członków grupy Monitoring są zapisywane do kolejki przy zmianie
stanu. Opiekun otrzymuje je tylko wtedy, gdy należy do tej grupy. Aktywna
aplikacja oznacza push, pozostałe konta otrzymują e-mail. Osobna komenda
`send_notifications` uruchamiana przez cron co 5 minut realizuje wysyłkę;
nie zastępuje ona `monitoring_sprawdz`, które nadal wykrywa zmiany stanu.
Kolejne sprawdzenie tego samego stanu nie tworzy ponownego alertu. Odbiór
raportu też sprawdza zmianę stanu, w tym powrót do OK.
Szczegóły: [kolejka i historia powiadomień](../notifications/README.md).

Migracja `0016` zapisuje nową wartość domyślną i walidator dodatniego interwału.
Nie przelicza ani nie nadpisuje interwałów istniejących usług.
