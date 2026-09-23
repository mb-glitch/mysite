#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# /media/pi/elemele/$kto/.skrypty/10_backup.sh
# Główny skrypt backupu - przechowywany na Raspberry Pi w katalogu domownika.
# Pobierany automatycznie przez telefon do ~/backup/ przed każdym wykonaniem.
# ==============================================================================
set -euo pipefail

BACKUP_DIR="$HOME/backup"
cd "$BACKUP_DIR"

# 1. Zabezpieczenie przed dublowaniem (jeśli poprzedni rclone copy jeszcze działa)
pgrep -f "rclone copy" >/dev/null && exit 0

# 2. Wczytanie konfiguracji głównej i indywidualnej
# Domyślne wartości:
PI_HOST="192.168.0.131"
PI_USER="smerf"
PI_PORT="22"
PI_NAME="pi"
DEST_BASE="/media/pi/elemele"
ROOT_PATH="/storage/emulated/0"
MONITOR_SERVER="https://host109829.xce.pl/api/monitoring/"
CONFIG_FILE="$BACKUP_DIR/.backup_user"
TOKEN_FILE="$BACKUP_DIR/.backup_token"
LOCK_FILE="$BACKUP_DIR/.last_backup_date"
RCLONE_FILTERS_FILE="$BACKUP_DIR/.rclone-filters.txt"

# Wczytaj backup.conf ze zsynchronizowanego katalogu usera jeśli istnieje
if [ -f "$BACKUP_DIR/backup.conf" ]; then
  # shellcheck source=/dev/null
  source "$BACKUP_DIR/backup.conf"
fi

# Obsługa argumentu --force
FORCE_RUN=false
for arg in "${@:-}"; do
  case "$arg" in
    -f|--force) FORCE_RUN=true ;;
    -h|--help)  echo "Użycie: $0 [--force]"; exit 0 ;;
  esac
done

# 3. Wczytaj dane użytkownika (kto) i upewnij się, że jest z małej litery
if [ ! -f "$CONFIG_FILE" ]; then
  echo "[BŁĄD] Brak pliku $CONFIG_FILE!"
  exit 1
fi

if grep -q "=" "$CONFIG_FILE"; then
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"
else
  KTO=$(cat "$CONFIG_FILE" | tr -d '\r\n ')
fi
KTO=$(echo "$KTO" | tr '[:upper:]' '[:lower:]')
KTO="${KTO,,}"

MONITOR_TOKEN=""
if [ -f "$TOKEN_FILE" ]; then
  MONITOR_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\r\n ')
fi

# 4. Blokada dzienna (max 1x na dobę, chyba że --force)
TODAY=$(date +'%F')
if ! $FORCE_RUN && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

# 5. Sprawdź obecność w domowym Wi-Fi z Raspberry Pi (timeout 3s)
if ! ssh -o ConnectTimeout=3 -o BatchMode=yes -p "$PI_PORT" "$PI_USER@$PI_HOST" "true" 2>/dev/null; then
  exit 0
fi

# 6. Blokada uśpienia procesora na czas kopiowania
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

# Upewnij się, że katalog docelowy istnieje na Pi (z wielkiej litery)
DEST_PATH="$DEST_BASE/$KTO/_backup_tel_sync"
DEST_REMOTE="$PI_NAME:$DEST_PATH"
ssh -p "$PI_PORT" "$PI_USER@$PI_HOST" "mkdir -p '$DEST_PATH'" 2>/dev/null || true

START_TIME=$(date +'%F %T')
echo "[$START_TIME] Rozpoczynam archiwalny backup rclone copy ($KTO)..."

# Filtry rclone
if [ ! -f "$RCLONE_FILTERS_FILE" ]; then
  cat > "$RCLONE_FILTERS_FILE" <<'EOF'
+ /DCIM/**
+ /Pictures/**
+ /Documents/**
+ /Download/**
+ /Music/**
+ /Android/media/**
- **.tmp
- **.swp
- **.bak
- **.nomedia
- **.thumbnails/**
- **Cache/**
- **cache/**
- **Thumbs.db
- **.DS_Store
- *
EOF
fi

RCLONE_PARAMS=(
  --filter-from "$RCLONE_FILTERS_FILE"
  --transfers 4
  --fast-list
  --modify-window 2s
  --retries 3
  -q
)

# 7. Główny backup pamięci wewnętrznej telefonu (rclone copy - bez kasowania na Pi)
COPY_SUCCESS=true

if ! rclone copy "$ROOT_PATH" "$DEST_REMOTE" "${RCLONE_PARAMS[@]}"; then
  COPY_SUCCESS=false
fi

# 8. Kopia karty SD (jeśli wykryta lub zdefiniowana w konfiguracji)
if [ -z "${SD_CARD:-}" ]; then
  # Trzy niezawodne metody wykrycia karty SD bez uprawnień root
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
  echo "[$START_TIME] Kopiuję kartę SD ($SD_CARD)..."
  if ! rclone copy "/storage/$SD_CARD" "$DEST_REMOTE/sd_card" "${RCLONE_PARAMS[@]}"; then
    COPY_SUCCESS=false
  fi
fi

END_TIME=$(date +'%F %T')

# ==============================================================================
# 9. MONITORING HTTPS + ZAPIS PLIKU LOCK
# ==============================================================================
if [ "$COPY_SUCCESS" = true ]; then
  echo "[$END_TIME] Backup zakończony pomyślnie dla $KTO!"

  # Zapisujemy lock z dzisiejszą datą w ~/backup/.last_backup_date
  echo "$TODAY" > "$LOCK_FILE"

  # Dopisanie do historii na Raspberry Pi
  ssh -p "$PI_PORT" "$PI_USER@$PI_HOST" \
    "echo '[$END_TIME] [OK] $KTO (backup zakończony sukcesem)' >> '$DEST_BASE/backup_telefony.log'" 2>/dev/null || true

  # Raport HTTPS do Django API
  if [ -n "${MONITOR_SERVER:-}" ] && [ -n "${MONITOR_TOKEN:-}" ]; then
    payload=$(jq -n \
      --arg user "$KTO" \
      --argjson code 0 \
      --arg date "$END_TIME" \
      '{status_code: $code, user: $user, message: {status: "OK", timestamp: $date}}')

    curl -s --max-time 10 -X POST "$MONITOR_SERVER" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$payload" >/dev/null 2>&1 || true
  fi

  termux-notification --title "Backup OK ($KTO)" --content "Zapisano pomyślnie na serwerze (rclone copy)" --id 999 2>/dev/null || true
  exit 0
else
  echo "[$END_TIME] Błąd podczas rclone copy dla $KTO!"

  # Raport błędu do Django API
  if [ -n "${MONITOR_SERVER:-}" ] && [ -n "${MONITOR_TOKEN:-}" ]; then
    payload=$(jq -n \
      --arg user "$KTO" \
      --argjson code 1 \
      --arg log "Błąd Rclone dla $KTO" \
      '{status_code: $code, user: $user, message: {error: $log}}')

    curl -s --max-time 10 -X POST "$MONITOR_SERVER" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$payload" >/dev/null 2>&1 || true
  fi

  termux-notification --title "Błąd backupu ($KTO)" --content "Wystąpił błąd kopiowania" --priority high --id 999 2>/dev/null || true
  exit 1
fi

