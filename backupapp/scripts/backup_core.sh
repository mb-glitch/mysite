#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# Termux -> Raspberry Pi Backup Agent
# VERSION: 2026-03-13 00:10
# ==============================================================================

set -uo pipefail

HOUR=$(date +%H)
case "$HOUR" in
    06|07|20|21) ;;
    *) exit 0 ;;
esac

# ==============================
# 1. KONFIGURACJA UŻYTKOWNIKA
# ==============================

TOKEN="db65dd43b5098151117a402f2ead9c31cf137dde"
KTO="maciek"

START_TIME=$(date +%s)
STATUS=0
# ==============================
# 2. KONFIGURACJA MINIMALNA
# ==============================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

SERVER="https://host109829.xce.pl/api/monitoring/"
DEST_BASE="gdrive:backup/$KTO"
RCLONE_BIN="/data/data/com.termux/files/usr/bin/rclone"
ROOT_PATH="/storage/emulated/0"

EXCLUDE_PATTERNS=('*~' '*.tmp' '*.swp' '*.bak' '.DS_Store' 'Thumbs.db' '*.nomedia' '.thumbnails/**')
LOG_DIR="$SCRIPT_DIR/backup_logs"
LOG_FILE="$LOG_DIR/backup.log"
SOURCE_DIR="$SCRIPT_DIR/backup"
DEST_DIR="$DEST_BASE/_backup_tel_sync"


mkdir -p "$SOURCE_DIR" "$LOG_DIR"


# ==============================
# 4. FUNKCJE
# ==============================
payload=$(jq -n \
    --argjson code "$STATUS" \
    '{status_code: $code, message: {}}')

# Funkcja logująca
log() { echo "$(date +"%F %T") $*" | tee -a "$LOG_FILE"; }

# Funcja symlink
make_link() {
    local src=$1
    local dest=$2
    if [ -d "$src" ]; then
        if [ ! -L "$dest" ] || [ "$(readlink "$dest")" != "$src" ]; then
            ln -snf "$src" "$dest"
            log "[DEBUG] Link: $(basename "$dest") -> połączono"
        fi
    fi
}

# Wysyłka monitoringu
send_report() {
    local payload="$1"
    
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    log "[DEBUG] Cały proces trwał: ${ELAPSED}s"
    payload=$(echo "$payload" | jq --argjson elapsed "$ELAPSED" \
    '. + {execute_time_ms: $elapsed}')
  
    curl -s -X POST "$SERVER" \
        -H "Authorization: Token $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload"
}

# ==============================
# 5. TWORZENIE SYMLINKÓW
# ==============================
log "[DEBUG] Synchronizacja pliku do wykonania"

CORE_SCRIPT="$SCRIPT_DIR/backup_core.sh"
TMP_SCRIPT="$SCRIPT_DIR/backup_core.tmp"


if $RCLONE_BIN copy "$DEST_BASE/backup_core.tmp" "$TMP_SCRIPT" >>"$LOG_FILE" 2>&1; then
    log "[DEBUG] Synchronizacja pliku wykonana"
    # Sprawdzamy czy pobrany plik różni się od obecnego
    if ! cmp -s "$TMP_SCRIPT" "$CORE_SCRIPT" && [ -s "$TMP_SCRIPT" ]; then
        log "[INFO] Wykryto nową wersję skryptu. Aktualizuję i restartuję..."
        chmod +x "$TMP_SCRIPT"
        mv "$TMP_SCRIPT" "$CORE_SCRIPT"
        send_report "$payload"
        # RESTART: Wywołujemy ten sam skrypt od nowa i kończymy obecną instancję
        exec "$SCRIPT_DIR/backup.sh" "$@"
    fi
else
    STATUS=1
    log "[ERROR] Błąd podczas pobierania pliku sh"
fi


log "[DEBUG] --- Backup start ---"

make_link "$ROOT_PATH/DCIM"          "$SOURCE_DIR/dcim"
make_link "$ROOT_PATH/Download"      "$SOURCE_DIR/download"
make_link "$ROOT_PATH/Music"         "$SOURCE_DIR/music"
make_link "$ROOT_PATH/Pictures"      "$SOURCE_DIR/pictures"
make_link "$ROOT_PATH/Documents"     "$SOURCE_DIR/documents"
make_link "$ROOT_PATH/Android/media" "$SOURCE_DIR/media"

# Tworzymy tablicę dostępnych SD / OTG
SD_CARDS=()
for d in ~/storage/external-*; do
    [ -d "$d" ] && SD_CARDS+=("$d")
done

# Wybieramy pierwszą kartę SD, jeśli istnieje
SD_CARD=""
if [ ${#SD_CARDS[@]} -gt 0 ]; then
    SD_CARD="${SD_CARDS[0]}"
    log "[DEBUG] Wykryto kartę SD: $SD_CARD"
else
    log "[WARNING] Brak dostępnej karty SD"
fi

if [ -n "$SD_CARD" ]; then

    mkdir -p "$SOURCE_DIR/$SD_CARD"

    make_link "/storage/$SD_CARD/DCIM"          "$SOURCE_DIR/$SD_CARD/dcim"
    make_link "/storage/$SD_CARD/Download"      "$SOURCE_DIR/$SD_CARD/download"
    make_link "/storage/$SD_CARD/Music"         "$SOURCE_DIR/$SD_CARD/music"
    make_link "/storage/$SD_CARD/Pictures"      "$SOURCE_DIR/$SD_CARD/pictures"
    make_link "/storage/$SD_CARD/Documents"     "$SOURCE_DIR/$SD_CARD/documents"
    make_link "/storage/$SD_CARD/Android/media" "$SOURCE_DIR/$SD_CARD/media"

fi

# ==============================
# 6. TEST SSH
# ==============================
if ! $RCLONE_BIN about $DEST_BASE >/dev/null 2>&1; then
    log "[ERROR] Brak połączenia z serwerem"
    exit 1
fi

log "[DEBUG] Połączenie OK"

# ==============================
# 7. BACKUP (RCLONE)
# ==============================

RCLONE_EXCLUDE=()

for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    RCLONE_EXCLUDE+=(--exclude "$pattern")
done

log "[DEBUG] Backup rclone start"

if "$RCLONE_BIN" copy "$SOURCE_DIR" "$DEST_DIR" \
    --links \
    --transfers 3 \
    --checkers 6 \
    --ignore-existing \
    --size-only \
    --max-age 5d \
    "${RCLONE_EXCLUDE[@]}" >>"$LOG_FILE" 2>&1; then

    STATUS=0
    log "[DEBUG] Backup zakończony sukcesem"
else
    STATUS=1
    log "[ERROR] Błąd podczas backupu"
fi

# ==============================
# 9. RAPORT DO DJANGO
# ==============================
log "[DEBUG] Wysyłanie raportu..."
if send_report "$payload"; then
  "$RCLONE_BIN" bisync "$LOG_FILE" "$DEST_BASE"
else
    log "[ERROR] Nie udało się wysłać raportu – log zostaje"
fi

exit 0
