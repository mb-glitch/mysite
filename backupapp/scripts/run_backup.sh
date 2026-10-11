#!/data/data/com.termux/files/usr/bin/bash
# Dyspozytor zadań backupu; raporty niewysłane pozostają w .monitoring-pending.
set -u
umask 077

BACKUP_DIR="$HOME/backup"
LOCK_DIR="$BACKUP_DIR/.locks"
LOG_DIR="$BACKUP_DIR/logs"
BACKUP_LOG="$LOG_DIR/backup.log"
PENDING_DIR="$BACKUP_DIR/.monitoring-pending"
mkdir -p "$LOCK_DIR" "$LOG_DIR" "$PENDING_DIR" || exit 1
cd "$BACKUP_DIR" || exit 1
if [ -f "$BACKUP_LOG" ] && [ "$(wc -c < "$BACKUP_LOG")" -gt 500000 ]; then
  tail -n 1000 "$BACKUP_LOG" > "$BACKUP_LOG.tmp" && mv "$BACKUP_LOG.tmp" "$BACKUP_LOG"
fi
exec >> "$BACKUP_LOG" 2>&1

TODAY=$(date +'%F')
LOCK_FILE="$BACKUP_DIR/.last_backup_date"
SSH_CONFIG="$BACKUP_DIR/.ssh.conf"
MONITOR_SERVER="https://host109829.xce.pl/api/monitoring/"
MONITOR_TOKEN=""
if [ -f "$BACKUP_DIR/.token.conf" ]; then
  chmod 600 "$BACKUP_DIR/.token.conf" || exit 1
  MONITOR_TOKEN=$(tr -d '\r\n ' < "$BACKUP_DIR/.token.conf")
fi
command -v jq >/dev/null || { echo "[BŁĄD] Brak jq: zainstaluj pkg install jq."; exit 1; }

log() { echo "[$(date +'%F %T')] [RUNNER] $*"; }

send_pending() {
  local report http_code curl_code failed=0
  for report in "$PENDING_DIR"/*.json; do
    [ -f "$report" ] || continue
    if [ -z "$MONITOR_TOKEN" ]; then
      log "Brak tokena; zachowuję niewysłany raport."
      failed=1
      break
    fi
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
      -X POST "$MONITOR_SERVER" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H 'Content-Type: application/json' --data-binary "@$report")
    curl_code=$?
    if [ "$curl_code" -eq 0 ] && [[ "$http_code" == 2?? ]]; then
      rm -f "$report"
      log "Monitoring przyjął raport: HTTP $http_code."
    else
      log "Nie wysłano raportu: curl=$curl_code, HTTP=$http_code; ponowię przy następnym uruchomieniu."
      failed=1
      break
    fi
  done
  return "$failed"
}

FAILED_TASKS='[]'
ALL_SUCCESS=true
add_failure() {
  local task="$1" code="$2" description="$3"
  FAILED_TASKS=$(jq -cn --argjson tasks "$FAILED_TASKS" --arg script "$task" \
    --argjson exit_code "$code" --arg description "$description" \
    '$tasks + [{script: $script, exit_code: $exit_code, description: $description}]') || exit 1
  ALL_SUCCESS=false
  log "BŁĄD: $task (kod: $code): $description"
}

queue_report() {
  local payload report temporary description
  if [ "$ALL_SUCCESS" = true ]; then
    payload=$(jq -cn --arg checked_at "$(date -Iseconds)" \
      '{status_code: 0, summary: "Wszystkie zadania zakończone sukcesem", checked_at: $checked_at}') || return 1
  else
    description=$(jq -r '[.[] | "\(.script) (kod: \(.exit_code)): \(.description)"] | join("; ")' <<< "$FAILED_TASKS") || return 1
    payload=$(jq -cn --arg error "$description" --argjson failed_tasks "$FAILED_TASKS" \
      --arg checked_at "$(date -Iseconds)" \
      '{status_code: 2, error: $error, failed_tasks: $failed_tasks, checked_at: $checked_at}') || return 1
  fi
  temporary=$(mktemp "$PENDING_DIR/$(date +%s)-XXXXXX.tmp") || return 1
  report="${temporary%.tmp}.json"
  printf '%s\n' "$payload" > "$temporary" && mv "$temporary" "$report"
}

# Najpierw ponów raporty, nawet jeśli backup wykonano już dzisiaj albo Pi jest niedostępne.
DELIVERY_FAILED=0
send_pending || DELIVERY_FAILED=1
FORCE=false
if [ "${1:-}" = '--force' ] || [ "${1:-}" = '-f' ]; then FORCE=true; fi
if [ "$FORCE" = false ] && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE")" = "$TODAY" ]; then
  exit "$DELIVERY_FAILED"
fi

KTO=unknown
if [ ! -f "$BACKUP_DIR/.user.conf" ]; then
  add_failure 'configuration' 1 'Brak .user.conf'
elif grep -q '=' "$BACKUP_DIR/.user.conf"; then
  source "$BACKUP_DIR/.user.conf" || add_failure 'configuration' 1 'Nie można wczytać .user.conf'
else
  KTO=$(tr -d '\r\n ' < "$BACKUP_DIR/.user.conf")
fi
KTO=$(printf '%s' "$KTO" | tr '[:upper:]' '[:lower:]')
if [ ! -f "$SSH_CONFIG" ]; then add_failure 'configuration' 1 'Brak .ssh.conf'; fi
if [ "$ALL_SUCCESS" = false ]; then
  queue_report && send_pending
  exit 1
fi

# Brak sieci domowej przed rozpoczęciem zadań jest pominięciem, a nie udanym backupem.
if ! ssh -F "$SSH_CONFIG" -o StrictHostKeyChecking=yes -o ConnectTimeout=3 -o BatchMode=yes pi true; then
  log 'Pi niedostępne: pomijam zadania.'
  exit "$DELIVERY_FAILED"
fi
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

RUNNER_OLD_HASH=$(md5sum "$BACKUP_DIR/run_backup.sh" | cut -d' ' -f1)
rclone --sftp-known-hosts-file "$HOME/.ssh/known_hosts" copy \
  "pi:/media/pi/elemele/$KTO/.skrypty" "$BACKUP_DIR" --fast-list -q
SYNC_CODE=$?
if [ "$SYNC_CODE" -ne 0 ]; then add_failure 'sync_scripts' "$SYNC_CODE" 'Nie udało się pobrać skryptów z Pi'; fi

mkdir -p "$HOME/.config/rclone" "$HOME/.ssh"
for config in .rclone.conf rclone.conf; do
  if [ -f "$BACKUP_DIR/$config" ]; then
    cp "$BACKUP_DIR/$config" "$HOME/.config/rclone/rclone.conf"
    chmod 600 "$HOME/.config/rclone/rclone.conf"
    break
  fi
done
if [ -f "$SSH_CONFIG" ]; then
  cp "$SSH_CONFIG" "$HOME/.ssh/config"
  chmod 600 "$HOME/.ssh/config"
fi
RUNNER_NEW_HASH=$(md5sum "$BACKUP_DIR/run_backup.sh" | cut -d' ' -f1)
if [ "$SYNC_CODE" -eq 0 ] && [ "$RUNNER_OLD_HASH" != "$RUNNER_NEW_HASH" ]; then
  exec bash "$BACKUP_DIR/run_backup.sh" "$@"
fi

FOUND_ANY=false
for script in "$BACKUP_DIR"/*.sh; do
  [ -f "$script" ] || continue
  script_name=$(basename "$script")
  [ "$script_name" = 'run_backup.sh' ] && continue
  FOUND_ANY=true
  script_lock="$LOCK_DIR/$script_name.done"
  if [ "$FORCE" = false ] && [ -f "$script_lock" ] && [ "$(cat "$script_lock")" = "$TODAY" ]; then
    log "Pomijam $script_name: już wykonany dzisiaj."
    continue
  fi
  log "Uruchamiam $script_name."
  if bash "$script" "$@"; then
    printf '%s\n' "$TODAY" > "$script_lock"
  else
    code=$?
    add_failure "$script_name" "$code" 'Zadanie zakończyło się błędem; szczegóły w backup.log'
  fi
done
if [ "$FOUND_ANY" = false ]; then add_failure 'tasks' 1 'Nie znaleziono żadnych skryptów roboczych'; fi

# Najpierw zapisz raport; blokada dzienna nie może zgubić nieprzekazanego wyniku.
queue_report || exit 1
if [ "$ALL_SUCCESS" = true ]; then printf '%s\n' "$TODAY" > "$LOCK_FILE"; fi
DELIVERY_FAILED=0
send_pending || DELIVERY_FAILED=1
if [ "$ALL_SUCCESS" = true ]; then
  termux-notification --title "Backup OK ($KTO)" --content 'Zadania zakończone pomyślnie.' --id 999 2>/dev/null || true
else
  termux-notification --title "Błąd backupu ($KTO)" --content "$FAILED_TASKS" --id 999 2>/dev/null || true
fi
rclone --sftp-known-hosts-file "$HOME/.ssh/known_hosts" copy \
  "$LOG_DIR" "pi:/media/pi/elemele/$KTO/logs" --fast-list -q || log 'Nie udało się przesłać logów na Pi.'
if [ "$ALL_SUCCESS" = false ]; then exit 1; fi
exit "$DELIVERY_FAILED"
