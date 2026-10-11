# ==============================================================================
# backupapp/views.py
# Aplikacja: BACKUPAPP
# Onboarding telefonów z użyciem Tokenu konta maszynowego DRF
# ==============================================================================
import base64
from pathlib import Path

import qrcode
from qrcode.image.svg import SvgPathImage
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.views.decorators.cache import never_cache
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
SCRIPT_VERSION = "2026-10-11 monitoring reports"


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
    token_obj = get_object_or_404(Token.objects.select_related('user'), key=token, user__is_active=True)
    invitation = BackupInvitation.objects.filter(token_link=token).first()
    
    if not invitation:
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

    # Stałe adresy środowiska:
    server_ip = getattr(settings, 'BACKUP_PI_HOST', '192.168.0.131')
    ssh_user = getattr(settings, 'BACKUP_PI_USER', 'smerf')
    status_url = request.build_absolute_uri(reverse('claim_invitation', args=[token]))
    script_url = request.build_absolute_uri(reverse('setup_phone', args=[token]))
    # Monitoring ZAWSZE na stały zewnętrzny/wewnętrzny URL produkcyjny:
    monitor_url = getattr(settings, 'URL_MONITORING', 'https://host109829.xce.pl/api/monitoring/')

    runner_script = (SCRIPT_DIR / 'run_backup.sh').read_text(encoding='utf-8')
    runner_script = runner_script.replace('https://host109829.xce.pl/api/monitoring/', monitor_url)

    bash_script = f"""#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
umask 077
echo "=== Instalacja backupu telefonu dla: {username} ==="
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
    StrictHostKeyChecking yes
    UserKnownHostsFile ~/.ssh/known_hosts
SSHEOF
chmod 600 ~/.ssh/config

echo "-> Autoryzacja SSH na Raspberry Pi ({ssh_user}@{server_ip}):"
echo "Przy pierwszym połączeniu porównaj odcisk klucza hosta z kluczem Raspberry Pi, zanim go zaakceptujesz."
ssh-copy-id -F ~/.ssh/config -o StrictHostKeyChecking=ask -i ~/.ssh/id_ed25519.pub pi < /dev/tty
chmod 600 ~/.ssh/known_hosts
ssh -F ~/.ssh/config -o StrictHostKeyChecking=yes pi "umask 077; mkdir -p '/media/pi/elemele/{username}/_backup_tel_sync' '/media/pi/elemele/{username}/.config/backup/scripts' '/media/pi/elemele/{username}/.config/backup/logs'; chmod 700 '/media/pi/elemele/{username}/.config/backup' '/media/pi/elemele/{username}/.config/backup/scripts' '/media/pi/elemele/{username}/.config/backup/logs'" < /dev/null

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
known_hosts_file = ~/.ssh/known_hosts
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


@never_cache
@login_required(login_url='backup_login')
def backup_dashboard(request):
    """
    Pulpit domowników:
    Prezentuje listę zaproszeń oraz stan usług pobrany z MonitoredService.
    """
    active_invites = BackupInvitation.objects.filter(is_used=False).select_related('user').order_by('user__username')
    services_data = []

    if MonitoredService:
        services = MonitoredService.objects.select_related('owner', 'service_user').all().order_by('owner__username', 'service_name')
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
        qr = qrcode.QRCode(box_size=4, border=4)
        qr.add_data(setup_url)
        qr.make(fit=True)
        svg = qr.make_image(image_factory=SvgPathImage).to_string()
        display_data.append({
            'user': invite.user.username,
            'setup_url': setup_url,
            'qr_data_uri': 'data:image/svg+xml;base64,' + base64.b64encode(svg).decode('ascii'),
        })

    return render(request, 'backup_dashboard.html', {
        'invitations': display_data,
        'services': services_data,
    })

