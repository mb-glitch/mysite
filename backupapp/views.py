# ==============================================================================
# backupapp/views.py
# Aplikacja: BACKUPAPP
# Onboarding telefonów z użyciem Tokenu konta maszynowego DRF
# ==============================================================================
import base64
import hashlib
import json
from pathlib import Path

import qrcode
from qrcode.image.svg import SvgPathImage
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token

from .models import BackupInvitation
from monitoring.models import is_dedicated_service_account

try:
    from monitoring.models import MonitoredService, LogEntry
except ImportError:
    MonitoredService = None
    LogEntry = None

SCRIPT_DIR = getattr(settings, 'BASE_DIR', Path('.')) / "backupapp" / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "run_backup.sh"
DEFAULT_MONITOR_URL = 'https://host109829.xce.pl/api/monitoring/'
SSH_KEY_INSTALL_COMMAND = (
    'set -eu; umask 077; mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"; '
    'touch "$HOME/.ssh/authorized_keys"; chmod 600 "$HOME/.ssh/authorized_keys"; '
    'IFS= read -r key; [ -n "$key" ]; '
    'grep -qxF "$key" "$HOME/.ssh/authorized_keys" || '
    'printf "\\n%s\\n" "$key" >> "$HOME/.ssh/authorized_keys"'
)


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def backup_script_info(request):
    script_url = request.build_absolute_uri(reverse('backup-core'))
    try:
        content = current_runner_script()
    except FileNotFoundError:
        return JsonResponse({"error": "run_backup.sh not found"}, status=404)
    return JsonResponse({
        "latest_version": hashlib.sha256(content.encode('utf-8')).hexdigest(),
        "script_url": script_url,
    })


def current_runner_script():
    content = SCRIPT_PATH.read_text(encoding='utf-8')
    monitor_url = getattr(settings, 'URL_MONITORING', DEFAULT_MONITOR_URL)
    return content.replace(DEFAULT_MONITOR_URL, monitor_url)


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_backup_script(request):
    try:
        content = current_runner_script()
        response = HttpResponse(content, content_type='text/x-sh; charset=utf-8')
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        return HttpResponse("Script not found", status=404)


@never_cache
def setup_phone(request, token):
    """
    Wydaje instalator dla telefonu:
    Token należy do konta technicznego (np. svc_maciek_backup).
    """
    token_obj = get_object_or_404(Token.objects.select_related('user'), key=token, user__is_active=True)
    invitation = get_object_or_404(BackupInvitation.objects.select_related('user'), token_link=token_obj.key, is_archived=False)
    if token_obj.user.is_staff or token_obj.user.is_superuser or token_obj.user.has_usable_password():
        raise Http404('Zaproszenie wymaga konta technicznego.')
    get_object_or_404(MonitoredService, service_user=token_obj.user, is_active=True)

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

    # Stałe adresy środowiska:
    server_ip = getattr(settings, 'BACKUP_PI_HOST', '192.168.0.131')
    ssh_user = getattr(settings, 'BACKUP_PI_USER', 'smerf')
    status_url = request.build_absolute_uri(reverse('claim_invitation', args=[token]))
    script_url = request.build_absolute_uri(reverse('setup_phone', args=[token]))
    runner_script = current_runner_script()

    bash_script = f"""#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077
echo "=== Instalacja backupu telefonu dla: {username} (SSH v2) ==="
export DEBIAN_FRONTEND=noninteractive
echo "-> Instaluję wymagane pakiety Termuxa..."
pkg install -y termux-api rclone openssh jq curl util-linux </dev/null
termux-setup-storage </dev/null 2>/dev/null || true
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 </dev/null
chmod 600 ~/.ssh/id_ed25519

cat > ~/.ssh/config << 'SSHEOF'
Host pi
    HostName {server_ip}
    User {ssh_user}
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
SSHEOF
chmod 600 ~/.ssh/config

echo "-> Autoryzacja SSH na Raspberry Pi ({ssh_user}@{server_ip}):"
echo "-> Wpisz hasło użytkownika {ssh_user}; od razu wgram klucz logowania."
if ! ssh -F ~/.ssh/config -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o BatchMode=no pi '{SSH_KEY_INSTALL_COMMAND}' < "$HOME/.ssh/id_ed25519.pub"; then
  echo "BŁĄD: Nie udało się wgrać klucza logowania na Pi."
  exit 1
fi
if ! ssh -F ~/.ssh/config -o BatchMode=yes -o PreferredAuthentications=publickey -o PasswordAuthentication=no -o IdentitiesOnly=yes pi true < /dev/null; then
  echo "BŁĄD: Logowanie kluczem do Pi nie działa; nie kończę instalacji."
  exit 1
fi
ssh -F ~/.ssh/config pi "umask 077; mkdir -p '/media/pi/elemele/{username}/_backup_tel_sync' '/media/pi/elemele/{username}/.config/backup/scripts' '/media/pi/elemele/{username}/.config/backup/logs'; chmod 700 '/media/pi/elemele/{username}/.config/backup' '/media/pi/elemele/{username}/.config/backup/scripts' '/media/pi/elemele/{username}/.config/backup/logs'" < /dev/null

BACKUP_DIR="$HOME/backup"
mkdir -p "$BACKUP_DIR" ~/.config/rclone
cp ~/.ssh/config "$BACKUP_DIR/.ssh.conf"
chmod 600 "$BACKUP_DIR/.ssh.conf"

cat > "$BACKUP_DIR/.user.conf" << USREOF
KTO="{username}"
PI_USER="{ssh_user}"
DEST_BASE="/media/pi/elemele"
USREOF

# Zapisujemy token konta maszynowego DRF:
[ ! -f "$BACKUP_DIR/.token.conf" ] || chmod 600 "$BACKUP_DIR/.token.conf"
echo '{token}' > "$BACKUP_DIR/.token.conf"
chmod 600 "$BACKUP_DIR/.token.conf"

cat > ~/.config/rclone/rclone.conf << 'EOF'
[pi]
type = sftp
host = {server_ip}
user = {ssh_user}
port = 22
key_file = ~/.ssh/id_ed25519
known_hosts_file = none
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
{runner_script}
RUNNER_EOF
chmod +x "$BACKUP_DIR/run_backup.sh"

REMOTE_RUNNER='/media/pi/elemele/{username}/.config/backup/scripts/run_backup.sh'
echo '-> Umieszczam aktualny run_backup.sh na Pi.'
rclone copyto "$BACKUP_DIR/run_backup.sh" "pi:$REMOTE_RUNNER.next" -q
ssh -F ~/.ssh/config -o BatchMode=yes pi "bash -n '$REMOTE_RUNNER.next' && chmod 700 '$REMOTE_RUNNER.next' && mv -f '$REMOTE_RUNNER.next' '$REMOTE_RUNNER'" < /dev/null

termux-job-scheduler --cancel --job-id 101 2>/dev/null || true
if ! termux-job-scheduler --job-id 101 --script "$BACKUP_DIR/run_backup.sh" --period-ms 3600000 --network unmetered --persisted true; then
  echo "BŁĄD: Nie udało się zaplanować backupu. Sprawdź instalację aplikacji Termux:API."
  exit 1
fi
curl -s "{status_url}" >/dev/null 2>&1 || true
rm -f "$HOME/setup.sh" 2>/dev/null || true
echo "=== GOTOWE! Backup dla {username} skonfigurowany. ==="
"""

    user_agent = request.headers.get('User-Agent', '').lower()
    if 'curl' in user_agent or 'wget' in user_agent or request.GET.get('raw'):
        return HttpResponse(bash_script, content_type='text/plain; charset=utf-8')

    one_liner = f"curl -fsSL {script_url} -o ~/setup.sh && bash ~/setup.sh"
    return render(request, 'setup.html', {'user': user, 'token': token, 'one_liner': one_liner, 'direct_url': script_url})


def claim_invitation(request, token):
    invitation = BackupInvitation.objects.filter(token_link=token, is_archived=False).first()
    if not invitation:
        token_obj = Token.objects.filter(key=token).first()
        if token_obj:
            invitation = BackupInvitation.objects.filter(token_link=token_obj.key, is_archived=False).first()

    if invitation:
        invitation.is_used = True
        invitation.save(update_fields=['is_used'])

    if request.GET.get('redirect') or ('text/html' in request.headers.get('Accept', '')):
        return redirect('backup_dashboard')

    return HttpResponse("OK")


@never_cache
@login_required(login_url='backup_login')
@require_POST
def create_mobile_token(request, invitation_id):
    invitations = BackupInvitation.objects.filter(is_used=False, is_archived=False, user__is_active=True)
    if not request.user.is_superuser:
        invitations = invitations.filter(user=request.user)
    invitation = get_object_or_404(invitations.select_related('user'), pk=invitation_id)
    if is_dedicated_service_account(invitation.user) or MonitoredService.objects.filter(service_user=invitation.user).exists():
        raise Http404('Zaproszenie nie należy do konta użytkownika.')
    Token.objects.get_or_create(user=invitation.user)
    return redirect('backup_dashboard')


def qr_data_uri(payload):
    qr = qrcode.QRCode(box_size=4, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    svg = qr.make_image(image_factory=SvgPathImage).to_string()
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode('ascii')


@never_cache
@login_required(login_url='backup_login')
def backup_dashboard(request):
    """
    Pulpit domowników:
    Prezentuje listę zaproszeń oraz stan usług pobrany z MonitoredService.
    """
    active_invites = BackupInvitation.objects.filter(is_used=False, is_archived=False).select_related('user').order_by('user__username')
    services_data = []

    if MonitoredService:
        services = MonitoredService.objects.filter(is_archived=False).select_related('owner', 'service_user').order_by('owner__username', 'service_name')
        for s in services:
            latest_log = s.logs.order_by('-checked_at', '-pk').first()
            service_status = s.status
            log_msg = ""
            if latest_log and isinstance(latest_log.message, dict):
                log_msg = latest_log.message.get('log') or latest_log.message.get('summary') or latest_log.message.get('error') or str(latest_log.message)
            elif latest_log:
                log_msg = str(latest_log.message)

            services_data.append({
                'service': s,
                'user': s.owner,
                'service_user': s.service_user,
                'status_code': service_status,
                'status_display': s.STATUS_LABELS[service_status],
                'last_check_at': s.last_check_at,
                'latest_log': latest_log,
                'log_content': log_msg,
                'is_healthy': service_status == 0,
            })

    display_data = []
    for invite in active_invites:
        setup_url = request.build_absolute_uri(reverse('setup_phone', args=[invite.token_link]))
        mobile_allowed = bool(
            (request.user.is_superuser or request.user.pk == invite.user_id)
            and invite.user.is_active and not is_dedicated_service_account(invite.user)
            and not MonitoredService.objects.filter(service_user=invite.user).exists()
        )
        mobile_token = Token.objects.filter(user=invite.user).first() if mobile_allowed else None
        mobile_qr = None
        if mobile_token:
            mobile_qr = qr_data_uri(json.dumps({
                'type': 'mysite-dashboard', 'version': 1,
                'server_url': request.build_absolute_uri('/').rstrip('/'),
                'token': mobile_token.key,
            }, separators=(',', ':')))
        display_data.append({
            'id': invite.pk,
            'user': invite.user.username,
            'setup_url': setup_url,
            'qr_data_uri': qr_data_uri(setup_url),
            'mobile_allowed': mobile_allowed,
            'mobile_qr_data_uri': mobile_qr,
        })

    return render(request, 'backup_dashboard.html', {
        'invitations': display_data,
        'services': services_data,
    })

