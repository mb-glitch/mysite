#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# Termux -> Raspberry Pi Backup Agent
# VERSION: 2026-03-13 00:10
# ==============================================================================

set -uo pipefail

# ==============================
# 1. KONFIGURACJA MINIMALNA
# ==============================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source "$SCRIPT_DIR/backup.conf"

# ==============================
# 2. KONFIGURACJE POZOSTAŁE
# ==============================
PI_USER="pi"
PI_HOST="192.168.5.148"
SERVER="https://192.168.5.148:8000"

DEST_BASE="/media/pi/elemele"

RCLONE_BIN="/data/data/com.termux/files/usr/bin/rclone"
SSH_BIN="/data/data/com.termux/files/usr/bin/ssh"

ROOT_PATH="/storage/emulated/0"
SOURCE_DIR="$SCRIPT_DIR/backup"

LOG_DIR="$SCRIPT_DIR/backup_logs"
LOG_FILE="$LOG_DIR/backup.log"

EXCLUDE_PATTERNS=('*~' '*.tmp' '*.swp' '*.bak' '.DS_Store' 'Thumbs.db' '*.nomedia')

mkdir -p "$SOURCE_DIR" "$LOG_DIR"

# Funkcja logująca
log() { echo "$(date +"%F %T") $*" | tee -a "$LOG_FILE"; }

# ==============================
# 4. FUNKCJA SYMLINK
# ==============================
make_link() {

    local src=$1
    local dest=$2

    if [ -d "$src" ]; then

        if [ ! -L "$dest" ] || [ "$(readlink "$dest")" != "$src" ]; then
            ln -snf "$src" "$dest"
            log "Link: $(basename "$dest") -> połączono"
        fi

    fi
}

# ==============================
# 5. TWORZENIE SYMLINKÓW
# ==============================
log "--- Backup start ---"

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
    echo "Wykryto kartę SD: $SD_CARD"
else
    echo "Brak dostępnej karty SD"
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
if ! $RCLONE_BIN about backup: >/dev/null 2>&1; then
    log "Brak połączenia z serwerem"
    exit 1
fi

log "Połączenie SSH OK"

# ==============================
# 7. BACKUP (RCLONE)
# ==============================
DEST_DIR="$DEST_BASE/$KTO/_backup_tel_sync"

RCLONE_DEST=":sftp,host=$PI_HOST,user=$PI_USER:$DEST_DIR"

RCLONE_EXCLUDE=""

for pattern in "${EXCLUDE_PATTERNS[@]}"; do
    RCLONE_EXCLUDE+=" --exclude $pattern"
done

log "Backup rclone start"

if $RCLONE_BIN copy "$SOURCE_DIR" "$RCLONE_DEST" \
    --links \
    --transfers 3 \
    --checkers 6 \
    --ignore-existing \
    --size-only \
    #  --max-age 30d \
    $RCLONE_EXCLUDE >>"$LOG_FILE" 2>&1; then

    STATUS="success"
    log "Backup zakończony sukcesem"

else

    STATUS="error"
    log "Błąd podczas backupu"

fi

# ==============================
# 8. STAN DYSKU
# ==============================
SD_FREE="none"

if [ -n "$SD_CARD" ]; then
    SD_PATH="/storage/$SD_CARD"
    SD_FREE=$(df -h "$SD_PATH" | awk 'NR==2 {print $5}')
    log "Zajęte miejsce SD ($SD_CARD): $SD_FREE"
fi

ROOT_FREE=$(df -h "$ROOT_PATH" | awk 'NR==2 {print $5}')

log "Zajęte miejsce telefon: $ROOT_FREE"

# ==============================
# 9. RAPORT DO DJANGO
# ==============================
log "Wysyłanie raportu..."

REPORT_BODY=$(cat "$LOG_FILE")

if curl -s -k -X POST "$SERVER/api/backup-report/" \
     -H "Authorization: Token $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{
       \"status\": \"$STATUS\",
       \"root_space\": \"$ROOT_FREE\",
       \"sd_space\": \"$SD_FREE\",
       \"log\": $(echo "$REPORT_BODY" | jq -Rs .)
     }" -o /dev/null; then

    log "Raport wysłany poprawnie"
    rm -f "$LOG_FILE"

else
    log "Nie udało się wysłać raportu – log zostaje"
fi

exit 0
