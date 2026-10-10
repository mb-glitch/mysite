#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# ~/backup/run_backup.sh - Lekki dyspozytor z per-skrypt lockami i logami
# Uruchamiany automatycznie przez termux-job-scheduler co 1 godzinę.
# Wszystkie pliki na telefonie znajdują się w jednym katalogu ~/backup.
# ==============================================================================
set -u

BACKUP_DIR="$HOME/backup"
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

LOCK_DIR="$BACKUP_DIR/.locks"
LOG_DIR="$BACKUP_DIR/logs"
BACKUP_LOG="$LOG_DIR/backup.log"
mkdir -p "$LOCK_DIR" "$LOG_DIR"

# Rotacja logu: jeśli backup.log przekracza 500 KB, zachowaj ostatnie 1000 linii
if [ -f "$BACKUP_LOG" ] && [ "$(wc -c < "$BACKUP_LOG" 2>/dev/null || echo 0)" -gt 500000 ]; then
  tail -n 1000 "$BACKUP_LOG" > "$BACKUP_LOG.tmp" && mv "$BACKUP_LOG.tmp" "$BACKUP_LOG"
fi

# Całe wyjście z run_backup.sh (stdout i stderr) trafia do wspólnego pliku logs/backup.log
exec >> "$BACKUP_LOG" 2>&1

LOCK_FILE="$BACKUP_DIR/.last_backup_date"
TODAY=$(date +'%F')
TIMESTAMP=$(date +'%F %T')
USER_FILE="$BACKUP_DIR/.user.conf"
TOKEN_FILE="$BACKUP_DIR/.token.conf"
SSH_CONFIG="$BACKUP_DIR/.ssh.conf"
# Domyślny endpoint odbiorcy raportów w aplikacji monitoring (DRF APIView: MonitoringReceiverView)
MONITOR_SERVER="https://host109829.xce.pl/api/monitoring/"

# 1. Odczytaj nazwę użytkownika (kto) i upewnij się, że jest z małej litery
if [ ! -f "$USER_FILE" ]; then
  echo "[$TIMESTAMP] [RUNNER] BŁĄD: Brak pliku $USER_FILE! Uruchom ponownie setup." >> "$BACKUP_LOG"
  exit 1
fi

if grep -q "=" "$USER_FILE"; then
  # shellcheck source=/dev/null
  source "$USER_FILE"
else
  KTO=$(cat "$USER_FILE" | tr -d '
 ')
fi
KTO=$(echo "$KTO" | tr '[:upper:]' '[:lower:]')

# Weryfikacja pliku konfiguracyjnego SSH (.ssh.conf) - jak nie ma to błąd skryptu!
if [ ! -f "$SSH_CONFIG" ]; then
  echo "[$TIMESTAMP] [RUNNER] BŁĄD: Brak wymaganego pliku konfiguracyjnego SSH: $SSH_CONFIG! Uruchom ponownie setup." >> "$BACKUP_LOG"
  exit 1
fi

MONITOR_TOKEN=""
if [ -f "$TOKEN_FILE" ]; then
  MONITOR_TOKEN=$(cat "$TOKEN_FILE" | tr -d '
 ')
fi

# 2. Blokada dzienna całego zestawu (jeśli dzisiaj WSZYSTKIE skrypty zakończone - wyjdź w 2 ms)
if [ "${1:-}" != "--force" ] && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

# 3. Sprawdź czy jesteśmy w domowym Wi-Fi z Raspberry Pi (timeout 2s, profil pi w .ssh.conf)
if ! ssh -F "$SSH_CONFIG" -o ConnectTimeout=2 -o BatchMode=yes pi "true" 2>/dev/null; then
  exit 0
fi

# 4. Zablokuj uśpienie procesora telefonu na czas wykonywania zadań
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

# 5. Auto-aktualizacja: pobierz indywidualne skrypty i configi z katalogu .skrypty na Raspberry Pi
RUNNER_OLD_HASH=""
[ -f "$BACKUP_DIR/run_backup.sh" ] && RUNNER_OLD_HASH=$(md5sum "$BACKUP_DIR/run_backup.sh" 2>/dev/null | cut -d' ' -f1 || true)

REMOTE_SCRIPTS="pi:/media/pi/elemele/$KTO/.skrypty"

echo "[$TIMESTAMP] [RUNNER] Synchronizuję skrypty z $REMOTE_SCRIPTS..." >> "$BACKUP_LOG"
rclone copy "$REMOTE_SCRIPTS" "$BACKUP_DIR" --fast-list -q 2>/dev/null || true

# Jeśli na Pi w katalogu użytkownika jest dedykowany .rclone.conf lub .ssh.conf, zaktualizuj je
if [ -f "$BACKUP_DIR/.rclone.conf" ]; then
  cp "$BACKUP_DIR/.rclone.conf" "$HOME/.config/rclone/rclone.conf"
  chmod 600 "$HOME/.config/rclone/rclone.conf"
elif [ -f "$BACKUP_DIR/rclone.conf" ]; then
  cp "$BACKUP_DIR/rclone.conf" "$HOME/.config/rclone/rclone.conf"
  chmod 600 "$HOME/.config/rclone/rclone.conf"
fi
if [ -f "$BACKUP_DIR/.ssh.conf" ]; then
  cp "$BACKUP_DIR/.ssh.conf" "$HOME/.ssh/config" 2>/dev/null || true
  chmod 600 "$HOME/.ssh/config" 2>/dev/null || true
fi

chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

# Bezpieczna samo-aktualizacja orkiestratora (run_backup.sh) jeśli plik na Pi uległ zmianie
RUNNER_NEW_HASH=""
[ -f "$BACKUP_DIR/run_backup.sh" ] && RUNNER_NEW_HASH=$(md5sum "$BACKUP_DIR/run_backup.sh" 2>/dev/null | cut -d' ' -f1 || true)

if [ -n "$RUNNER_OLD_HASH" ] && [ -n "$RUNNER_NEW_HASH" ] && [ "$RUNNER_OLD_HASH" != "$RUNNER_NEW_HASH" ]; then
  echo "[$TIMESTAMP] [RUNNER] [UPDATE] Zaktualizowano run_backup.sh do nowej wersji! Przeładowuję proces..." >> "$BACKUP_LOG"
  exec bash "$BACKUP_DIR/run_backup.sh" "$@"
fi

# 6. Uruchomienie skryptów z NIEZALEŻNYMI BLOKADAMI DZIENNYMI (Per-script lock)
# Skrypt wykonany dzisiaj pomyślnie NIE będzie powtarzany co godzinę!
ALL_SUCCESS=true
declare -a FAILED_SCRIPTS=()
declare -a SUCCESS_SCRIPTS=()
declare -a SKIPPED_SCRIPTS=()

echo "--- [START SESJI: $TIMESTAMP dla $KTO] ---" >> "$BACKUP_LOG"

for script in $(ls "$BACKUP_DIR"/*.sh 2>/dev/null | sort); do
  [ -f "$script" ] || continue
  script_name=$(basename "$script")
  # Pomijamy samego dyspozytora
  [ "$script_name" = "run_backup.sh" ] && continue

  script_lock="$LOCK_DIR/${script_name}.done"

  # Jeśli ten konkretny skrypt wykonał się już dzisiaj z sukcesem -> POMIŃ GO!
  if [ "${1:-}" != "--force" ] && [ -f "$script_lock" ] && [ "$(cat "$script_lock" 2>/dev/null)" = "$TODAY" ]; then
    echo "[$TIMESTAMP] [RUNNER] [POMINIĘTO] $script_name został już dzisiaj wykonany sukcesem." >> "$BACKUP_LOG"
    SKIPPED_SCRIPTS+=("$script_name")
    continue
  fi

  echo "[$TIMESTAMP] [RUNNER] [START] Uruchamiam: $script_name..." >> "$BACKUP_LOG"

  if bash "$script" "$@" >> "$BACKUP_LOG" 2>&1; then
    echo "[$TIMESTAMP] [RUNNER] [OK] $script_name zakończony pomyślnie." >> "$BACKUP_LOG"
    echo "$TODAY" > "$script_lock"
    SUCCESS_SCRIPTS+=("$script_name")
  else
    EXIT_CODE=$?
    echo "[$TIMESTAMP] [RUNNER] [BŁĄD] $script_name wysypał się (kod: $EXIT_CODE)! Zobacz logs/backup.log" >> "$BACKUP_LOG"
    FAILED_SCRIPTS+=("$script_name (kod: $EXIT_CODE)")
    ALL_SUCCESS=false
  fi
done

END_TIMESTAMP=$(date +'%F %T')

# 7. Synchronizacja zwrotna: Przerzucenie logów z telefonu na Raspberry Pi
DEST_LOGS_REMOTE="pi:/media/pi/elemele/$KTO/logs"
echo "[$END_TIMESTAMP] [RUNNER] Przesyłam logi na Raspberry Pi ($DEST_LOGS_REMOTE)..." >> "$BACKUP_LOG"
rclone copy "$LOG_DIR" "$DEST_LOGS_REMOTE" --fast-list -q 2>/dev/null || true

# 8. Bezpieczne przygotowanie wyciągu z logu bez błędów sed
LOG_PAYLOAD=$(tail -n 25 "$BACKUP_LOG" 2>/dev/null | tr '
' ' ' | tr '"' "'" | tr '\' '/' || true)

# 9. Obsługa wyników i raportowanie (Django API, Raspberry Pi log, Termux notification)
if [ "$ALL_SUCCESS" = true ]; then
  echo "$TODAY" > "$LOCK_FILE"
  echo "[$END_TIMESTAMP] [RUNNER] Wszystkie zadania dla $KTO zakończone sukcesem." >> "$BACKUP_LOG"

  # Zapis w historii na Raspberry Pi przez profil pi w .ssh.conf
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=3 -o BatchMode=yes pi     "echo '[$END_TIMESTAMP] [OK] $KTO (wszystkie skrypty OK)' >> '/media/pi/elemele/backup_telefony.log'" 2>/dev/null || true

  # Raport do aplikacji monitoring (/monitoring/ DRF APIView)
  if [ -n "$MONITOR_TOKEN" ]; then
    echo "[$END_TIMESTAMP] [RUNNER] Wysyłam meldunek do $MONITOR_SERVER..." >> "$BACKUP_LOG"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$MONITOR_SERVER"       -H "Authorization: Token $MONITOR_TOKEN"       -H "Content-Type: application/json"       -d "{"status_code": 0, "summary": "Wszystkie skrypty OK", "log": "$LOG_PAYLOAD"}" || echo "FAIL")
    echo "[$END_TIMESTAMP] [RUNNER] Odpowiedź serwera monitoringu: HTTP $HTTP_CODE" >> "$BACKUP_LOG"
  fi

  termux-notification --title "Backup OK ($KTO)" --content "Wszystkie zadania zakończone pomyślnie." --id 999 2>/dev/null || true
else
  FAILED_LIST="${FAILED_SCRIPTS[*]}"
  echo "[$END_TIMESTAMP] [RUNNER] UWAGA! Następujące skrypty zawiodły: $FAILED_LIST" >> "$BACKUP_LOG"

  # Zapis błędu w historii na Raspberry Pi z DOKŁADNĄ nazwą skryptu przez .ssh.conf
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=3 -o BatchMode=yes pi     "echo '[$END_TIMESTAMP] [BŁĄD] $KTO: wysypał się skrypt: $FAILED_LIST' >> '/media/pi/elemele/backup_telefony.log'" 2>/dev/null || true

  # Raport o błędzie (status_code 2: critical) do aplikacji monitoring
  if [ -n "$MONITOR_TOKEN" ]; then
    echo "[$END_TIMESTAMP] [RUNNER] Wysyłam meldunek o błędzie do $MONITOR_SERVER..." >> "$BACKUP_LOG"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$MONITOR_SERVER"       -H "Authorization: Token $MONITOR_TOKEN"       -H "Content-Type: application/json"       -d "{"status_code": 2, "error": "Wysypał się skrypt: $FAILED_LIST", "log": "$LOG_PAYLOAD"}" || echo "FAIL")
    echo "[$END_TIMESTAMP] [RUNNER] Odpowiedź serwera monitoringu: HTTP $HTTP_CODE" >> "$BACKUP_LOG"
  fi

  # Wyraźne powiadomienie na telefonie z nazwą skryptu
  termux-notification --title "Błąd backupu ($KTO)"     --content "Wysypał się skrypt: $FAILED_LIST. Zobacz ~/backup/logs/backup.log"     --priority high --id 999 2>/dev/null || true
fi

