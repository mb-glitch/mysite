#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# Termux -> Raspberry Pi Backup Launcher
# VERSION: 2026-03-12 21:00
# ==============================================================================

set -uo pipefail

# ==============================
# KONFIGURACJA
# ==============================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

source "$SCRIPT_DIR/backup.conf"

CORE_SCRIPT="$SCRIPT_DIR/backup_core.sh"
TMP_SCRIPT="$SCRIPT_DIR/backup_core.tmp"

mkdir -p "$LOG_DIR"

log() { echo "$(date +"%F %T") $*" | tee -a "$LOG_FILE"; }

# ==============================
# SPRAWDZENIE GODZINY
# ==============================
HOUR=$(date +%H)
if [ "$HOUR" -ne "$RUN_HOUR" ]; then
    exit 0
fi

# ==============================
# POBRANIE INFO O WERSJI
# ==============================
INFO_JSON=$(curl -s -k -H "Authorization: Token $TOKEN" \
"$SERVER/api/backup-script-info/" || echo "{}")

LATEST_VERSION=$(echo "$INFO_JSON" | jq -r '.latest_version // empty')
SCRIPT_URL=$(echo "$INFO_JSON" | jq -r '.script_url // empty')

LATEST_VERSION=$(echo "$LATEST_VERSION" | tr -d '[:space:]')

# ==============================
# WERSJA LOKALNA backup_core.sh
# ==============================
LOCAL_VERSION=""

if [ -f "$CORE_SCRIPT" ]; then
    LOCAL_VERSION=$(grep -m1 -oP '^# VERSION: \K.*' "$CORE_SCRIPT" | tr -d '[:space:]' || echo "")
fi

# ==============================
# AUTO UPDATE CORE
# ==============================
if [ -n "$LATEST_VERSION" ] && [ "$LOCAL_VERSION" != "$LATEST_VERSION" ] && [ -n "$SCRIPT_URL" ]; then

    log "[INFO] Aktualizacja backup_core.sh"
    log "[INFO] Lokalna: $LOCAL_VERSION"
    log "[INFO] Serwer : $LATEST_VERSION"

    if curl -s -k -H "Authorization: Token $TOKEN" \
        "$SERVER$SCRIPT_URL" -o "$TMP_SCRIPT"; then

        if [ -s "$TMP_SCRIPT" ] && head -n1 "$TMP_SCRIPT" | grep -q "bash"; then

            chmod +x "$TMP_SCRIPT"
            mv "$TMP_SCRIPT" "$CORE_SCRIPT"

            log "[INFO] backup_core.sh zaktualizowany"

        else
            rm -f "$TMP_SCRIPT"
            log "[WARN] Pobrany plik jest niepoprawny"
        fi

    else
        log "[WARN] Nie udało się pobrać nowej wersji"
    fi
fi

# ==============================
# URUCHOMIENIE CORE
# ==============================
if [ -f "$CORE_SCRIPT" ]; then
    exec bash "$CORE_SCRIPT"
else
    log "[ERROR] backup_core.sh nie istnieje"
    exit 1
fi
