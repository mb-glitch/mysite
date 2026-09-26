#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# /media/pi/elemele/$kto/.skrypty/10_backup.sh
# Skrypt roboczy: Kopia zdjęć i plików przez Rclone copy (Worker)
# Uruchamiany automatycznie przez orkiestratora ~/backup/run_backup.sh
# ==============================================================================
# WAŻNE - ZARZĄDZANIE BLOKADAMI (LOCK):
# 10_backup.sh NIE dotyka pliku .last_backup_date ani nie wysyła raportów do API!
# Blokadami (.locks/10_backup.sh.done, zbiorczym .last_backup_date),
# logowaniem sesji oraz powiadomieniami zarządza wyłącznie run_backup.sh.
# Zadaniem tego skryptu jest wyłącznie wykonanie kopii i zwrócenie exit 0 lub 1.
# ==============================================================================
set -euo pipefail

BACKUP_DIR="$HOME/backup"
LOG_DIR="$BACKUP_DIR/logs"
BACKUP_LOG="$LOG_DIR/backup.log"
mkdir -p "$BACKUP_DIR" "$LOG_DIR"
cd "$BACKUP_DIR"

# Całe wyjście skryptu roboczego (stdout i stderr) trafia do wspólnego pliku logs/backup.log
exec >> "$BACKUP_LOG" 2>&1

# 1. Zabezpieczenie przed dublowaniem (jeśli poprzedni rclone copy jeszcze działa)
pgrep -f "rclone copy" >/dev/null && {
  echo "[$(date +'%F %T')] [10_backup.sh] [POMINIĘTO] Inny proces rclone copy jest już uruchomiony." >> "$BACKUP_LOG"
  exit 0
}

# 2. Wczytanie konfiguracji użytkownika
CONFIG_FILE="$BACKUP_DIR/.user.conf"
SSH_CONFIG="$BACKUP_DIR/.ssh.conf"
RCLONE_FILTERS_FILE="$BACKUP_DIR/.rclone-filters.conf"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "[$(date +'%F %T')] [10_backup.sh] [BŁĄD] Brak pliku $CONFIG_FILE! Uruchom ponownie setup." >> "$BACKUP_LOG"
  exit 1
fi

if grep -q "=" "$CONFIG_FILE"; then
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
else
  KTO=$(cat "$CONFIG_FILE" | tr -d '\r\n ')
fi
KTO=$(echo "$KTO" | tr '[:upper:]' '[:lower:]')

# Weryfikacja pliku konfiguracji połączenia SSH z Pi (.ssh.conf) - jak nie ma to błąd skryptu!
if [ ! -f "$SSH_CONFIG" ]; then
  echo "[$(date +'%F %T')] [10_backup.sh] [BŁĄD] Brak wymaganego pliku konfiguracyjnego SSH: $SSH_CONFIG!" >> "$BACKUP_LOG"
  exit 1
fi

# Weryfikacja pliku filtrów rclone (.rclone-filters.conf) - jak nie ma to błąd skryptu!
if [ ! -f "$RCLONE_FILTERS_FILE" ]; then
  echo "[$(date +'%F %T')] [10_backup.sh] [BŁĄD] Brak wymaganego pliku filtrów rclone: $RCLONE_FILTERS_FILE!" >> "$BACKUP_LOG"
  exit 1
fi

PI_NAME="pi"
DEST_BASE="${DEST_BASE:-/media/pi/elemele}"
ROOT_PATH="/storage/emulated/0"

START_TIME=$(date +'%F %T')
echo "[$START_TIME] [10_backup.sh] Rozpoczynam archiwalny backup rclone copy ($KTO)..." >> "$BACKUP_LOG"

# 3. Upewnij się, że katalog docelowy istnieje na Raspberry Pi (przez profil w .ssh.conf)
DEST_PATH="$DEST_BASE/$KTO/_backup_tel_sync"
DEST_REMOTE="$PI_NAME:$DEST_PATH"
ssh -F "$SSH_CONFIG" -o ConnectTimeout=3 -o BatchMode=yes "$PI_NAME" "mkdir -p '$DEST_PATH'" 2>/dev/null || true

# 4. Parametry rclone (zawsze z pliku filtrów .rclone-filters.conf)
RCLONE_PARAMS=(
  --filter-from "$RCLONE_FILTERS_FILE"
  --transfers 4
  --fast-list
  --modify-window 2s
  --retries 3
  -q
)

# 5. Główny backup pamięci wewnętrznej telefonu (rclone copy - bez kasowania na Pi)
COPY_SUCCESS=true

if ! rclone copy "$ROOT_PATH" "$DEST_REMOTE" "${RCLONE_PARAMS[@]}"; then
  echo "[$(date +'%F %T')] [10_backup.sh] [BŁĄD] rclone copy pamięci wewnętrznej nie powiódł się!" >> "$BACKUP_LOG"
  COPY_SUCCESS=false
fi

# 6. Kopia karty SD (jeśli wykryta lub zdefiniowana w konfiguracji)
if [ -z "${SD_CARD:-}" ]; then
  if [ -f /proc/mounts ]; then
    SD_DETECTED=$(grep -oE '/storage/[0-9A-Za-z_-]+' /proc/mounts 2>/dev/null | grep -vE '/storage/(emulated|self)' | head -n1 || true)
    [ -n "$SD_DETECTED" ] && SD_CARD=$(basename "$SD_DETECTED")
  fi
  if [ -z "${SD_CARD:-}" ]; then
    SD_DETECTED=$(df 2>/dev/null | grep -oE '/storage/[0-9A-Za-z_-]+' | grep -vE '/storage/(emulated|self)' | head -n1 || true)
    [ -n "$SD_DETECTED" ] && SD_CARD=$(basename "$SD_DETECTED")
  fi
fi

if [ "$COPY_SUCCESS" = true ] && [ -n "${SD_CARD:-}" ] && [ -d "/storage/$SD_CARD" ]; then
  echo "[$START_TIME] [10_backup.sh] Kopiuję kartę SD ($SD_CARD)..." >> "$BACKUP_LOG"
  if ! rclone copy "/storage/$SD_CARD" "$DEST_REMOTE/sd_card" "${RCLONE_PARAMS[@]}"; then
    echo "[$(date +'%F %T')] [10_backup.sh] [BŁĄD] rclone copy karty SD nie powiódł się!" >> "$BACKUP_LOG"
    COPY_SUCCESS=false
  fi
fi

END_TIME=$(date +'%F %T')

# 7. Zwrócenie kodu wyjścia do orkiestratora (run_backup.sh)
if [ "$COPY_SUCCESS" = true ]; then
  echo "[$END_TIME] [10_backup.sh] [SUKCES] 10_backup.sh ($KTO) zakończony pomyślnie." >> "$BACKUP_LOG"
  exit 0
else
  echo "[$END_TIME] [10_backup.sh] [BŁĄD] 10_backup.sh ($KTO) zakończony niepowodzeniem!" >> "$BACKUP_LOG"
  exit 1
fi

