#!/data/data/com.termux/files/usr/bin/bash
set -u

BACKUP_DIR="$HOME/backup"
LOCK_DIR="$BACKUP_DIR/.locks"
LOG_DIR="$BACKUP_DIR/logs"
BACKUP_LOG="$LOG_DIR/backup.log"
MONITOR_URL="https://host109829.xce.pl/api/monitoring/"

mkdir -p "$LOCK_DIR" "$LOG_DIR"

# Funkcja logująca jednocześnie na ekran i do pliku
log() {
  local msg="[$(date +'%Y-%m-%d %H:%M:%S')] $*"
  echo "$msg"
  echo "$msg" >> "$BACKUP_LOG"
}

log "================ START RUN_BACKUP ================"

TODAY=$(date +'%Y-%m-%d')
KTO="unknown"
[ -f "$BACKUP_DIR/.user.conf" ] && source "$BACKUP_DIR/.user.conf"
MONITOR_TOKEN=""
[ -f "$BACKUP_DIR/.token.conf" ] && MONITOR_TOKEN=$(cat "$BACKUP_DIR/.token.conf" | tr -d '\r\n ')

log "Użytkownik: $KTO"

# 1. Test połączenia z Raspberry Pi
log "1. Test połączenia SSH z Raspberry Pi (Host: pi)..."
SSH_OUT=$(ssh -F "$BACKUP_DIR/.ssh.conf" -o ConnectTimeout=5 -o BatchMode=yes pi "echo SSH_CONNECTED" 2>&1)
SSH_EXIT=$?

if [ $SSH_EXIT -ne 0 ] || [ "$SSH_OUT" != "SSH_CONNECTED" ]; then
  log "❌ BRAK POŁĄCZENIA Z PI (Kod błędu: $SSH_EXIT)"
  log "Szczegóły błędu SSH: $SSH_OUT"
  log "Upewnij się, że jesteś w domowej sieci Wi-Fi i Pi działa."
  log "================ KONIEC (BRAK PI) ================"
  exit 0
fi
log "✅ Połączenie z Pi działa poprawnie."

# 2. Blokada uśpienia Androida
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

# 3. Pobieranie / aktualizacja skryptów z Pi
log "2. Sprawdzanie skryptów w pi:/media/pi/elemele/$KTO/.skrypty ..."
rclone copy "pi:/media/pi/elemele/$KTO/.skrypty" "$BACKUP_DIR" -v >> "$BACKUP_LOG" 2>&1 || true
chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

# 4. Wykonywanie skryptów backupu
ALL_OK=true
FAILED_SCRIPTS=()
FOUND_ANY=false

# Sprawdzamy czy wymuszamy uruchomienie (ręczne wywołanie z terminala (to nie -wyłączam ([ -t 1 ])  ) lub flaga -f)
FORCE=false
if [ "${1:-}" = "-f" ] || [ "${1:-}" = "--force" ]; then
  FORCE=true
  log "Wymuszono uruchomienie (ignoruję blokady dzienne)."
fi

for script in "$BACKUP_DIR"/*.sh; do
  [ -f "$script" ] || continue
  sname=$(basename "$script")
  [ "$sname" = "run_backup.sh" ] && continue
  FOUND_ANY=true

  slock="$LOCK_DIR/$sname.done"
  if [ "$FORCE" = false ] && [ -f "$slock" ] && [ "$(cat "$slock" 2>/dev/null)" = "$TODAY" ]; then
    log "-> Pomijam $sname (już wykonany dzisiaj $TODAY)"
    continue
  fi

  log "-> Uruchamiam: $sname ..."
  if bash "$script" >> "$BACKUP_LOG" 2>&1; then
    log "  [OK] $sname zakończony sukcesem."
    echo "$TODAY" > "$slock"
  else
    log "  [BŁĄD] $sname zakończył się błędem!"
    FAILED_SCRIPTS+=("$sname")
    ALL_OK=false
  fi
done

if [ "$FOUND_ANY" = false ]; then
  log "⚠️ Nie znaleziono żadnych dodatkowych skryptów *.sh w $BACKUP_DIR."
fi

# 5. Wysłanie meldunku do Django /monitoring/ z tokenem konta technicznego DRF
MONITOR_URL="http://$(grep HostName "$BACKUP_DIR/.ssh.conf" | awk '{print $2}'):8000/monitoring/"

if [ -n "$MONITOR_TOKEN" ]; then
  log "3. Wysyłanie meldunku do serwera ($MONITOR_URL)..."
  if [ "$ALL_OK" = true ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$MONITOR_URL" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"status_code": 0, "summary": "Wszystkie skrypty OK"}' || echo "FAIL")
    log "Odpowiedź serwera: HTTP $HTTP_CODE"
    termux-notification --title "Backup OK ($KTO)" --content "Zakończono pomyślnie." --id 999 2>/dev/null || true
  else
    FLIST="${FAILED_SCRIPTS[*]}"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$MONITOR_URL" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"status_code\": 2, \"error\": \"Błąd skryptów: $FLIST\"}" || echo "FAIL")
    log "Odpowiedź serwera: HTTP $HTTP_CODE"
    termux-notification --title "Błąd backupu ($KTO)" --content "Zawiodły: $FLIST" --id 999 2>/dev/null || true
  fi
else
  log "⚠️ Brak tokena w $BACKUP_DIR/.token.conf - pomijam raport do monitoringu."
fi

log "================ KONIEC RUN_BACKUP ================"
