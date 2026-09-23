#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# ~/backup/run_backup.sh - Lekki auto-aktualizator i dyspozytor zadań na telefonie
# Uruchamiany automatycznie przez termux-job-scheduler co 1 godzinę.
# Wszystkie pliki na telefonie znajdują się w jednym katalogu ~/backup.
# ==============================================================================
set -u

BACKUP_DIR="$HOME/backup"
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

LOCK_FILE="$BACKUP_DIR/.last_backup_date"
TODAY=$(date +'%F')
USER_FILE="$BACKUP_DIR/.backup_user"
PI_HOST="192.168.0.131"
PI_USER="smerf"

# 1. Odczytaj nazwę użytkownika (kto) i upewnij się, że jest z małej litery
if [ ! -f "$USER_FILE" ]; then
  echo "BŁĄD: Brak pliku $USER_FILE! Uruchom ponownie setup."
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
KTO="${KTO,,}"

# 2. Jeśli dzisiaj backup już się wykonał - wyjdź natychmiast (oszczędność baterii)
if [ "${1:-}" != "--force" ] && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

# 3. Sprawdź czy jesteśmy w domowym Wi-Fi (timeout 2s)
if ! ssh -o ConnectTimeout=2 -o BatchMode=yes -o StrictHostKeyChecking=no "$PI_USER@$PI_HOST" "true" 2>/dev/null; then
  exit 0
fi

# 4. Zablokuj uśpienie procesora telefonu na czas wykonywania zadań
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

# 5. Auto-aktualizacja: pobierz indywidualne skrypty i configi z katalogu .skrypty na Raspberry Pi
REMOTE_SCRIPTS="pi:/media/pi/elemele/$KTO/.skrypty"

echo "Pobieram najnowsze skrypty z $REMOTE_SCRIPTS do $BACKUP_DIR..."
rclone copy "$REMOTE_SCRIPTS" "$BACKUP_DIR" --fast-list -q 2>/dev/null || true

# Jeśli na Pi w katalogu użytkownika jest dedykowany rclone.conf, zaktualizuj go
if [ -f "$BACKUP_DIR/rclone.conf" ]; then
  cp "$BACKUP_DIR/rclone.conf" "$HOME/.config/rclone/rclone.conf"
  chmod 600 "$HOME/.config/rclone/rclone.conf"
fi

chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

# 6. Uruchom alfabetycznie wszystkie zadania *.sh (np. 10_backup.sh, 20_inne.sh)
ALL_SUCCESS=true

for script in $(ls "$BACKUP_DIR"/*.sh 2>/dev/null | sort); do
  [ -f "$script" ] || continue
  script_name=$(basename "$script")
  # Pomijamy dyspozytora
  [ "$script_name" = "run_backup.sh" ] && continue

  echo "=========================================="
  echo "Uruchamiam: $script_name dla $KTO"
  echo "=========================================="

  if ! bash "$script" "$@"; then
    echo "[OSTRZEŻENIE] $script_name zgłosił błąd!"
    ALL_SUCCESS=false
  fi
done

# 7. Po sukcesie wszystkich zadań zapisz dzisiejszą datę (lock)
if [ "$ALL_SUCCESS" = true ]; then
  echo "$TODAY" > "$LOCK_FILE"
  echo "Wszystkie zadania wykonane pomyślnie dla $KTO."
fi

