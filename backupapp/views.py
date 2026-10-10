# ==============================================================================
# backupapp/views.py
# Aplikacja: BACKUPAPP
# ==============================================================================
from pathlib import Path
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.conf import settings
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token

from .models import BackupInvitation

try:
    from monitoring.models import MonitoredService, LogEntry
except ImportError:
    MonitoredService = None
    LogEntry = None

SCRIPT_DIR = getattr(settings, 'BASE_DIR', Path('.')) / "backupapp" / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "10_backup.sh"
RCLONE_CONF_PATH = SCRIPT_DIR / "rclone.conf"
SCRIPT_VERSION = "2026-10-10 14:00"


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def backup_script_info(request):
    script_url = request.build_absolute_uri(reverse('backup-core'))
    return JsonResponse({"latest_version": SCRIPT_VERSION, "script_url": script_url})


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_backup_script(request):
    try:
        with open(SCRIPT_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='text/x-sh; charset=utf-8')
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        return HttpResponse("Script not found", status=404)


def get_rclone_conf(request):
    try:
        with open(RCLONE_CONF_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='text/x-sh; charset=utf-8')
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        return HttpResponse("rclone.conf not found", status=404)


def setup_phone(request, token):
    invitation = BackupInvitation.objects.filter(token_link=token).first()
    
    if not invitation:
        token_obj = get_object_or_404(Token, key=token)
        invitation = BackupInvitation.objects.filter(token_link=token_obj.key).first()
        if not invitation:
            invitation = BackupInvitation.objects.create(
                user=token_obj.user,
                token_link=token_obj.key,
                is_used=False
            )

    user = invitation.user
    username = user.username.lower()

    if invitation.is_used:
        user_agent = request.headers.get('User-Agent', '').lower()
        if 'curl' in user_agent or 'wget' in user_agent or request.GET.get('raw'):
            return HttpResponse(
                f'echo "BŁĄD: Zaproszenie dla {username} zostało już wykorzystane!"\nexit 1\n',
                content_type='text/plain; charset=utf-8',
                status=410
            )
        return render(request, 'setup.html', {'user': user, 'is_used': True}, status=410)

    server_ip = getattr(settings, 'BACKUP_PI_HOST', '192.168.0.131')
    ssh_user = getattr(settings, 'BACKUP_PI_USER', 'smerf')
    status_url = request.build_absolute_uri(reverse('claim_invitation', args=[token]))
    script_url = request.build_absolute_uri(reverse('setup_phone', args=[token]))
    monitor_url = getattr(settings, 'URL_MONITORING', 'https://host109829.xce.pl/api/monitoring/')

    # Szablon Basha bezpieczny dla interpretera Pythona (bez konfliktów f-stringów)
    bash_template = """#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
echo "=== Instalacja backupu telefonu dla: __USERNAME__ ==="
export DEBIAN_FRONTEND=noninteractive
pkg install -y -o Dpkg::Options::="--force-confold" termux-api rclone openssh jq curl </dev/null 2>/dev/null || pkg install -y termux-api rclone openssh jq curl </dev/null
termux-setup-storage </dev/null 2>/dev/null || true
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 </dev/null
chmod 600 ~/.ssh/id_ed25519

cat > ~/.ssh/config << 'SSHEOF'
Host pi
    HostName __SERVER_IP__
    User __SSH_USER__
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
SSHEOF
chmod 600 ~/.ssh/config

echo "-> Autoryzacja SSH na Raspberry Pi (__SSH_USER__@__SERVER_IP__):"
ssh-copy-id -o StrictHostKeyChecking=no -i ~/.ssh/id_ed25519.pub __SSH_USER__@__SERVER_IP__ < /dev/tty || true
ssh -F ~/.ssh/config pi "mkdir -p '/media/pi/elemele/__USERNAME__/_backup_tel_sync' '/media/pi/elemele/__USERNAME__/.skrypty' '/media/pi/elemele/__USERNAME__/logs'" < /dev/null 2>/dev/null || true

BACKUP_DIR="$HOME/backup"
mkdir -p "$BACKUP_DIR" ~/.config/rclone
cp ~/.ssh/config "$BACKUP_DIR/.ssh.conf"
chmod 600 "$BACKUP_DIR/.ssh.conf"

cat > "$BACKUP_DIR/.user.conf" << USREOF
KTO="__USERNAME__"
PI_USER="__SSH_USER__"
DEST_BASE="/media/pi/elemele"
USREOF

echo '__TOKEN__' > "$BACKUP_DIR/.token.conf"

cat > ~/.config/rclone/rclone.conf << 'EOF'
[pi]
type = sftp
host = __SERVER_IP__
user = __SSH_USER__
port = 22
key_file = ~/.ssh/id_ed25519
shell_type = unix
EOF
chmod 600 ~/.config/rclone/rclone.conf
cp ~/.config/rclone/rclone.conf "$BACKUP_DIR/.rclone.conf"
chmod 600 "$BACKUP_DIR/.rclone.conf"

cat > "$BACKUP_DIR/.rclone-filters.conf" << 'EOF'
+ /DCIM/**
+ /Pictures/**
+ /Documents/**
+ /Download/**
- **.tmp
- **.thumbnails/**
- **cache/**
- *
EOF

cat > "$BACKUP_DIR/run_backup.sh" << 'RUNNER_EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -u

BACKUP_DIR="$HOME/backup"
mkdir -p "$BACKUP_DIR"
cd "$BACKUP_DIR"

LOCK_DIR="$BACKUP_DIR/.locks"
LOG_DIR="$BACKUP_DIR/logs"
BACKUP_LOG="$LOG_DIR/backup.log"
mkdir -p "$LOCK_DIR" "$LOG_DIR"

if [ -f "$BACKUP_LOG" ] && [ "$(wc -c < "$BACKUP_LOG" 2>/dev/null || echo 0)" -gt 500000 ]; then
  tail -n 1000 "$BACKUP_LOG" > "$BACKUP_LOG.tmp" && mv "$BACKUP_LOG.tmp" "$BACKUP_LOG"
fi

exec >> "$BACKUP_LOG" 2>&1

LOCK_FILE="$BACKUP_DIR/.last_backup_date"
TODAY=$(date +'%F')
TIMESTAMP=$(date +'%F %T')
USER_FILE="$BACKUP_DIR/.user.conf"
TOKEN_FILE="$BACKUP_DIR/.token.conf"
SSH_CONFIG="$BACKUP_DIR/.ssh.conf"
MONITOR_SERVER="__MONITOR_URL__"

if [ ! -f "$USER_FILE" ]; then
  echo "[$TIMESTAMP] [RUNNER] BŁĄD: Brak pliku $USER_FILE!"
  exit 1
fi

if grep -q "=" "$USER_FILE"; then
  source "$USER_FILE"
else
  KTO=$(cat "$USER_FILE" | tr -d '\r\n ')
fi
KTO=$(echo "$KTO" | tr '[:upper:]' '[:lower:]')

if [ ! -f "$SSH_CONFIG" ]; then
  echo "[$TIMESTAMP] [RUNNER] BŁĄD: Brak pliku $SSH_CONFIG!"
  exit 1
fi

MONITOR_TOKEN=""
if [ -f "$TOKEN_FILE" ]; then
  MONITOR_TOKEN=$(cat "$TOKEN_FILE" | tr -d '\r\n ')
fi

if [ "${1:-}" != "--force" ] && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

if ! ssh -F "$SSH_CONFIG" -o ConnectTimeout=2 -o BatchMode=yes pi "true" 2>/dev/null; then
  exit 0
fi

termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

REMOTE_SCRIPTS="pi:/media/pi/elemele/$KTO/.skrypty"
echo "[$TIMESTAMP] [RUNNER] Synchronizuję skrypty z $REMOTE_SCRIPTS..."
rclone copy "$REMOTE_SCRIPTS" "$BACKUP_DIR" --fast-list -q 2>/dev/null || true

chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

ALL_SUCCESS=true
declare -a FAILED_SCRIPTS=()

echo "--- [START SESJI: $TIMESTAMP dla $KTO] ---"

for script in $(ls "$BACKUP_DIR"/*.sh 2>/dev/null | sort); do
  [ -f "$script" ] || continue
  script_name=$(basename "$script")
  [ "$script_name" = "run_backup.sh" ] && continue

  script_lock="$LOCK_DIR/${script_name}.done"

  if [ "${1:-}" != "--force" ] && [ -f "$script_lock" ] && [ "$(cat "$script_lock" 2>/dev/null)" = "$TODAY" ]; then
    echo "[$TIMESTAMP] [RUNNER] [POMINIĘTO] $script_name wykonany dzisiaj."
    continue
  fi

  echo "[$TIMESTAMP] [RUNNER] [START] Uruchamiam: $script_name..."

  if bash "$script" "$@" >> "$BACKUP_LOG" 2>&1; then
    echo "[$TIMESTAMP] [RUNNER] [OK] $script_name zakończony pomyślnie."
    echo "$TODAY" > "$script_lock"
  else
    EXIT_CODE=$?
    echo "[$TIMESTAMP] [RUNNER] [BŁĄD] $script_name wysypał się (kod: $EXIT_CODE)!"
    FAILED_SCRIPTS+=("$script_name")
    ALL_SUCCESS=false
  fi
done

END_TIMESTAMP=$(date +'%F %T')

DEST_LOGS_REMOTE="pi:/media/pi/elemele/$KTO/logs"
echo "[$END_TIMESTAMP] [RUNNER] Przesyłam logi na Raspberry Pi ($DEST_LOGS_REMOTE)..."
rclone copy "$LOG_DIR" "$DEST_LOGS_REMOTE" --fast-list -q 2>/dev/null || true

LOG_PAYLOAD=$(tail -n 25 "$BACKUP_LOG" 2>/dev/null | tr '\n' ' ' | tr '"' "'" | tr '\\' '/' || true)

if [ "$ALL_SUCCESS" = true ]; then
  echo "$TODAY" > "$LOCK_FILE"
  echo "[$END_TIMESTAMP] [RUNNER] Wszystkie zadania dla $KTO zakończone sukcesem."

  if [ -n "$MONITOR_TOKEN" ]; then
    echo "[$END_TIMESTAMP] [RUNNER] Wysyłam meldunek do $MONITOR_SERVER..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$MONITOR_SERVER" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"status_code\": 0, \"summary\": \"Wszystkie skrypty OK\", \"log\": \"$LOG_PAYLOAD\"}" || echo "FAIL")
    echo "[$END_TIMESTAMP] [RUNNER] Odpowiedź serwera monitoringu: HTTP $HTTP_CODE"
  fi

  termux-notification --title "Backup OK ($KTO)" --content "Zakończono pomyślnie." --id 999 2>/dev/null || true
else
  FAILED_LIST="${FAILED_SCRIPTS[*]}"
  echo "[$END_TIMESTAMP] [RUNNER] UWAGA! Następujące skrypty zawiodły: $FAILED_LIST"

  if [ -n "$MONITOR_TOKEN" ]; then
    echo "[$END_TIMESTAMP] [RUNNER] Wysyłam meldunek o błędzie do $MONITOR_SERVER..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "$MONITOR_SERVER" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"status_code\": 2, \"error\": \"Wysypał się skrypt: $FAILED_LIST\", \"log\": \"$LOG_PAYLOAD\"}" || echo "FAIL")
    echo "[$END_TIMESTAMP] [RUNNER] Odpowiedź serwera monitoringu: HTTP $HTTP_CODE"
  fi

  termux-notification --title "Błąd backupu ($KTO)" --content "Zawiodły: $FAILED_LIST" --priority high --id 999 2>/dev/null || true
fi
RUNNER_EOF
chmod +x "$BACKUP_DIR/run_backup.sh"

termux-job-scheduler --cancel --job-id 101 2>/dev/null || true
termux-job-scheduler --job-id 101 --script "$BACKUP_DIR/run_backup.sh" --period 3600000 --network unmetered --persisted true 2>/dev/null || true
curl -s "__STATUS_URL__" >/dev/null 2>&1 || true
rm -f "$HOME/setup.sh" 2>/dev/null || true
echo "=== GOTOWE! Backup dla __USERNAME__ skonfigurowany. ==="
"""

    bash_script = (
        bash_template
        .replace("__USERNAME__", username)
        .replace("__SERVER_IP__", server_ip)
        .replace("__SSH_USER__", ssh_user)
        .replace("__TOKEN__", token)
        .replace("__MONITOR_URL__", monitor_url)
        .replace("__STATUS_URL__", status_url)
    )

    user_agent = request.headers.get('User-Agent', '').lower()
    if 'curl' in user_agent or 'wget' in user_agent or request.GET.get('raw'):
        return HttpResponse(bash_script, content_type='text/plain; charset=utf-8')

    one_liner = f"curl -sL {script_url} -o ~/setup.sh && bash ~/setup.sh"
    return render(request, 'setup.html', {'user': user, 'token': token, 'one_liner': one_liner, 'direct_url': script_url})


def claim_invitation(request, token):
    invitation = BackupInvitation.objects.filter(token_link=token).first()
    if not invitation:
        token_obj = Token.objects.filter(key=token).first()
        if token_obj:
            invitation = BackupInvitation.objects.filter(user=token_obj.user).first()

    if invitation:
        invitation.is_used = True
        invitation.save()

    return HttpResponse("OK")


def backup_dashboard(request):
    active_invites = BackupInvitation.objects.filter(is_used=False).select_related('user').order_by('user__username')
    display_data = []

    for invite in active_invites:
        user_name = invite.user.username.lower()
        token_key = invite.token_link
        setup_url = request.build_absolute_uri(reverse('setup_phone', args=[token_key]))
        claim_url = request.build_absolute_uri(reverse('claim_invitation', args=[token_key]))
        one_liner = f"curl -sL {setup_url} -o ~/setup.sh && bash ~/setup.sh"

        display_data.append({
            'user': user_name,
            'token': token_key,
            'one_liner': one_liner,
            'setup_url': setup_url,
            'claim_url': claim_url,
            'created_at': invite.created_at.strftime('%Y-%m-%d %H:%M') if invite.created_at else '',
        })

    services_data = []
    if MonitoredService:
        for s in MonitoredService.objects.all():
            latest = s.logs.first()
            services_data.append({
                'service': s,
                'status_display': s.status_display,
                'last_check_at': s.last_check_at,
                'latest_log': latest,
            })

    return render(request, 'backup_dashboard.html', {
        'invitations': display_data,
        'has_active': len(display_data) > 0,
        'services': services_data,
    })
