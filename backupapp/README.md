# Zaproszenia i backup telefonu

Zaproszenie rozdziela trzy role:

- **Użytkownik aplikacji (KTO)** — pole `BackupInvitation.user`. Określa użytkownika
  telefonu, wartość `KTO` w `.user.conf` i katalog docelowy skryptu `run_backup.sh`.
  Instalator zachowuje dotychczasowe zamienianie nazwy użytkownika na małe litery.
- **Konto techniczne** — dedykowane konto bez hasła, którego token DRF służy
  instalatorowi i wysyłaniu raportów telefonu. W adminie można wskazać jego nazwę;
  tworzenie zaproszenia przez model domyślnie używa `svc_<użytkownik>_backup`.
- **Opiekun usługi monitoringu** — dla nowej usługi backupu jest nim zawsze
  istniejący użytkownik `maciek`, niezależnie od użytkownika zaproszenia.
  Jeśli konto `maciek` nie istnieje, utworzenie nowej usługi jest blokowane
  z czytelnym komunikatem, bez przypisywania jej przypadkowemu użytkownikowi.

Przykład: zaproszenie dla `krysia` tworzy instalator z `KTO="krysia"` i katalogiem
`/media/pi/elemele/krysia/_backup_tel_sync`; opiekunem nowej usługi jest `maciek`.
Nowa usługa oczekuje raportów co godzinę, zgodnie z harmonogramem backupu.

Edycja istniejącego zaproszenia zachowuje opiekuna, interwał i token jego usługi.
Opiekuna można zmienić niezależnie w adminie monitoringu; nie blokuje to linku
instalacyjnego telefonu. Nie wykonuje się automatycznego przenoszenia starych usług.
Istniejące zabezpieczenia kont technicznych, unikalność zaproszeń, archiwizacja
oraz reguły usuwania kont i powiązanych raportów pozostają w mocy.

Dodatkowy QR logowania do aplikacji Android zawiera token **użytkownika zaproszenia**,
nie token opiekuna monitoringu ani telefonu. Dostęp do dashboardów nadal zależy od
jego grup. Szczegóły aplikacji i API: [android/README.md](../android/README.md).

Migracja `0011_alter_backupinvitation_user` zmienia wyłącznie etykietę i opis pola
`user`. Nie przepisuje danych ani właścicieli usług. Została przygotowana do
wdrożenia; zastosowanie jej do trwałej bazy wymaga osobnego zatwierdzenia.

Sprawdzenia lokalne korzystają z tymczasowej bazy SQLite:

```sh
venv/bin/python manage.py check --settings=mysite.settings.dev
venv/bin/python manage.py test --settings=mysite.settings.dev
venv/bin/python manage.py makemigrations --check --dry-run --settings=mysite.settings.dev
```
