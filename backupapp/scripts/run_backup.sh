#!/data/data/com.termux/files/usr/bin/bash
# BACKUP_SINGLE_TASK_V1
# Jeden skrypt: aktualizacja, kopia telefonu i raportowanie do monitoringu.
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

log() { echo "[$(date +'%F %T')] [RUNNER] $*"; }

# Deskryptor 9 przechowuje blokadę przez cały przebieg, także po `exec bash`
# przy aktualizacji runnera. System zwalnia ją również po nagłym zakończeniu.
if [ "${BACKUP_RUNNER_REEXEC:-}" = '1' ]; then
  unset BACKUP_RUNNER_REEXEC
  if ! flock -n 9; then
    log 'Nie można przejąć blokady po aktualizacji runnera.'
    exit 1
  fi
else
  if ! command -v flock >/dev/null 2>&1; then
    pkg install -y util-linux </dev/null || { log 'Brak flock; nie uruchamiam backupu bez blokady.'; exit 1; }
  fi
  exec 9>"$LOCK_DIR/run_backup.lock" || exit 1
  if ! flock -n 9; then
    log 'Inna instancja run_backup.sh już działa; pomijam to uruchomienie.'
    exit 0
  fi
fi

# Usuń stare powiadomienie wyniku backupu, tworzone przez poprzednie wersje.
if command -v termux-notification-remove >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  timeout 3s termux-notification-remove 999 >/dev/null 2>&1 || true
fi

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
if ! rclone help backend sftp 2>/dev/null | grep -- '--sftp-ssh' >/dev/null; then
  add_failure 'configuration' 1 'Zbyt stara wersja rclone: brak obsługi zewnętrznego SSH'
  queue_report && send_pending
  exit 1
fi

# Rclone korzysta z tej samej konfiguracji i weryfikacji klucza hosta co ssh.
export RCLONE_SFTP_SSH="ssh -F $SSH_CONFIG -o BatchMode=yes -o StrictHostKeyChecking=yes pi"

DEST_BASE="${DEST_BASE:-/media/pi/elemele}"
REMOTE_USER_DIR="$DEST_BASE/$KTO"
REMOTE_BACKUP_DIR="$REMOTE_USER_DIR/.config/backup"
REMOTE_SCRIPT_DIR="$REMOTE_BACKUP_DIR/scripts"
REMOTE_LOG_DIR="$REMOTE_BACKUP_DIR/logs"

# Brak sieci domowej przed rozpoczęciem zadań jest pominięciem, a nie udanym backupem.
if ! ssh -F "$SSH_CONFIG" -o StrictHostKeyChecking=yes -o ConnectTimeout=3 -o BatchMode=yes pi true; then
  log 'Pi niedostępne: pomijam zadania.'
  exit "$DELIVERY_FAILED"
fi

# Przygotuj katalogi na Pi i jednorazowo zachowaj starsze logi.
if ssh -F "$SSH_CONFIG" -o StrictHostKeyChecking=yes -o BatchMode=yes pi "
  set -e
  umask 077
  mkdir -p '$REMOTE_SCRIPT_DIR' '$REMOTE_LOG_DIR' &&
  chmod 700 '$REMOTE_BACKUP_DIR' '$REMOTE_SCRIPT_DIR' '$REMOTE_LOG_DIR' &&
  if [ -d '$REMOTE_USER_DIR/logs' ] && [ ! -e '$REMOTE_BACKUP_DIR/.legacy-logs-imported' ]; then
    cp -Rn '$REMOTE_USER_DIR/logs/.' '$REMOTE_LOG_DIR/' &&
    touch '$REMOTE_BACKUP_DIR/.legacy-logs-imported' || exit 1
  fi
  if [ -f '$REMOTE_SCRIPT_DIR/run_backup.sh' ]; then
    chmod 700 '$REMOTE_SCRIPT_DIR/run_backup.sh'
  fi
" < /dev/null; then
  :
else
  code=$?
  add_failure 'prepare_remote' "$code" 'Nie udało się przygotować katalogów skryptów i logów na Pi'
  queue_report && send_pending
  exit 1
fi
termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

UPDATED_SCRIPT=$(mktemp "$BACKUP_DIR/.run_backup-XXXXXX") || exit 1
if rclone copyto "pi:$REMOTE_SCRIPT_DIR/run_backup.sh" "$UPDATED_SCRIPT" -q; then
  if ! grep -qFx '# BACKUP_SINGLE_TASK_V1' "$UPDATED_SCRIPT" || ! bash -n "$UPDATED_SCRIPT"; then
    add_failure 'sync_script' 1 'Skrypt na Pi jest niezgodny albo ma błąd składni'
  elif ! cmp -s "$BACKUP_DIR/run_backup.sh" "$UPDATED_SCRIPT"; then
    if chmod 700 "$UPDATED_SCRIPT" && mv -f "$UPDATED_SCRIPT" "$BACKUP_DIR/run_backup.sh"; then
      UPDATED_SCRIPT=''
      BACKUP_RUNNER_REEXEC=1 exec bash "$BACKUP_DIR/run_backup.sh" "$@"
    else
      add_failure 'sync_script' 1 'Nie udało się zainstalować run_backup.sh'
    fi
  fi
else
  code=$?
  add_failure 'sync_script' "$code" 'Nie udało się pobrać run_backup.sh z Pi'
fi
[ -z "$UPDATED_SCRIPT" ] || rm -f "$UPDATED_SCRIPT"

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
FILTERS_FILE="$BACKUP_DIR/.rclone-filters.conf"
PHONE_STORAGE_ROOT="${BACKUP_STORAGE_ROOT:-/storage}"
if [ ! -f "$FILTERS_FILE" ]; then
  add_failure 'configuration' 1 'Brak .rclone-filters.conf'
else
  DEST_PATH="$REMOTE_USER_DIR/_backup_tel_sync"
  if ssh -F "$SSH_CONFIG" -o StrictHostKeyChecking=yes -o ConnectTimeout=3 -o BatchMode=yes pi "mkdir -p '$DEST_PATH'"; then
    RCLONE_PARAMS=(--filter-from "$FILTERS_FILE" --transfers 4 --fast-list --modify-window 2s --retries 3 -q)
    log "Rozpoczynam kopię telefonu ($KTO)."
    if rclone copy "$PHONE_STORAGE_ROOT/emulated/0" "pi:$DEST_PATH" "${RCLONE_PARAMS[@]}"; then
      log 'Kopia pamięci telefonu zakończona.'
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
      if [ -n "${SD_CARD:-}" ] && [ -d "$PHONE_STORAGE_ROOT/$SD_CARD" ]; then
        log "Kopiuję kartę SD ($SD_CARD)."
        if rclone copy "$PHONE_STORAGE_ROOT/$SD_CARD" "pi:$DEST_PATH/sd_card" "${RCLONE_PARAMS[@]}"; then
          log 'Kopia karty SD zakończona.'
        else
          code=$?
          add_failure 'backup_sd' "$code" 'Kopia karty SD nie powiodła się'
        fi
      fi
    else
      code=$?
      add_failure 'backup_phone' "$code" 'Kopia pamięci telefonu nie powiodła się; szczegóły w backup.log'
    fi
  else
    code=$?
    add_failure 'backup_destination' "$code" 'Nie udało się przygotować katalogu kopii na Pi'
  fi
fi

# Po udanej kopii usuń wycofany skrypt roboczy z telefonu.
if [ "$ALL_SUCCESS" = true ]; then
  rm -f "$BACKUP_DIR/10_backup.sh" "$LOCK_DIR/10_backup.sh.done"
fi

# Najpierw zapisz raport; blokada dzienna nie może zgubić nieprzekazanego wyniku.
queue_report || exit 1
if [ "$ALL_SUCCESS" = true ]; then printf '%s\n' "$TODAY" > "$LOCK_FILE"; fi
DELIVERY_FAILED=0
send_pending || DELIVERY_FAILED=1
rclone copy \
  "$LOG_DIR" "pi:$REMOTE_LOG_DIR" --fast-list -q || log 'Nie udało się przesłać logów na Pi.'
if [ "$ALL_SUCCESS" = false ]; then exit 1; fi
exit "$DELIVERY_FAILED"
