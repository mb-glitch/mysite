# ==============================================================================
# backupapp/views.py
# Aplikacja: BACKUPAPP
# Onboarding telefonów z użyciem Tokenu konta maszynowego DRF
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
SCRIPT_VERSION = "2026-09-26 12:00"


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
    """
    Wydaje instalator dla telefonu:
    Token należy do konta technicznego (np. svc_maciek_backup).
    """
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

    # Zamiast zgadywać z request.get_host():
    server_ip = getattr(settings, 'BACKUP_PI_HOST', '192.168.0.131')
    ssh_user = getattr(settings, 'BACKUP_PI_USER', 'smerf') 
    status_url = request.build_absolute_uri(reverse('claim_invitation', args=[token]))
    script_url = request.build_absolute_uri(reverse('setup_phone', args=[token]))
    monitor_url = request.build_absolute_uri('/monitoring/')

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
LOCK_DIR="$BACKUP_DIR/.locks"
LOG_DIR="$BACKUP_DIR/logs"
BACKUP_LOG="$LOG_DIR/backup.log"
MONITOR_URL="https://host109829.xce.pl/api/monitoring/"

mkdir -p "$LOCK_DIR" "$LOG_DIR"

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

# 1. Test połączenia SSH z Raspberry Pi
log "1. Test połączenia SSH z Raspberry Pi (Host: pi)..."
SSH_OUT=$(ssh -F "$BACKUP_DIR/.ssh.conf" -o ConnectTimeout=5 -o BatchMode=yes pi "echo SSH_CONNECTED" 2>&1)
SSH_EXIT=$?

if [ $SSH_EXIT -ne 0 ] || [ "$SSH_OUT" != "SSH_CONNECTED" ]; then
  log "❌ BRAK POŁĄCZENIA Z PI (Kod błędu SSH: $SSH_EXIT)"
  log "Szczegóły błędu SSH: $SSH_OUT"
  log "Upewnij się, że jesteś w domowej sieci Wi-Fi i Pi działa."
  log "================ KONIEC (BRAK PI) ================"
  exit 0
fi
log "✅ Połączenie z Pi działa poprawnie."

termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

# 2. Synchronizacja skryptów z Pi
log "2. Sprawdzanie skryptów w pi:/media/pi/elemele/$KTO/.skrypty ..."
rclone copy "pi:/media/pi/elemele/$KTO/.skrypty" "$BACKUP_DIR" -v >> "$BACKUP_LOG" 2>&1 || true
chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

ALL_OK=true
FAILED_SCRIPTS=()
FOUND_ANY=false

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

# 3. Meldunek do DRF /monitoring/ z Tokenem Konta Maszynowego
if [ -n "$MONITOR_TOKEN" ]; then
  log "3. Wysyłanie meldunku do serwera (__MONITOR_URL__)..."
  if [ "$ALL_OK" = true ]; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "__MONITOR_URL__" \
      -H "Authorization: Token $MONITOR_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"status_code": 0, "summary": "Wszystkie skrypty OK"}' || echo "FAIL")
    log "Odpowiedź serwera: HTTP $HTTP_CODE"
    termux-notification --title "Backup OK ($KTO)" --content "Zakończono pomyślnie." --id 999 2>/dev/null || true
  else
    FLIST="${FAILED_SCRIPTS[*]}"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 -X POST "__MONITOR_URL__" \
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
            invitation = BackupInvitation.objects.filter(token_link=token_obj.key).first()

    if invitation:
        invitation.is_used = True
        invitation.save(update_fields=['is_used'])

    if request.GET.get('redirect') or ('text/html' in request.headers.get('Accept', '')):
        return redirect('backup_dashboard')

    return HttpResponse("OK")


def backup_dashboard(request):
    """
    Pulpit domowników:
    Prezentuje listę zaproszeń oraz stan usług pobrany z MonitoredService.
    """
    active_invites = BackupInvitation.objects.filter(is_used=False).select_related('user').order_by('user__username')
    services_data = []

    if MonitoredService:
        services = MonitoredService.objects.select_related('user', 'service_user').all().order_by('user__username', 'service_name')
        for s in services:
            latest_log = s.logs.order_by('-checked_at').first()
            log_msg = ""
            if latest_log and isinstance(latest_log.message, dict):
                log_msg = latest_log.message.get('log') or latest_log.message.get('summary') or latest_log.message.get('error') or str(latest_log.message)
            elif latest_log:
                log_msg = str(latest_log.message)

            services_data.append({
                'service': s,
                'user': s.user,
                'service_user': s.service_user,
                'status_code': latest_log.status_code if latest_log else s.last_status,
                'status_display': s.status_display,
                'last_check_at': s.last_check_at,
                'latest_log': latest_log,
                'log_content': log_msg,
                'is_healthy': s.is_healthy,
            })

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

    return render(request, 'backup_dashboard.html', {
        'invitations': display_data,
        'has_active': len(display_data) > 0,
        'services': services_data,
    })

