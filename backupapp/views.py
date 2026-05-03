from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from django.http import HttpResponse, HttpResponseForbidden, FileResponse, JsonResponse
from django.shortcuts import render, get_object_or_404

from rest_framework.authtoken.models import Token
from .models import BackupInvitation, BackupAgent


from django.shortcuts import render
from django.contrib.auth.decorators import login_required

from pathlib import Path
from django.urls import reverse
from django.utils import timezone
from django.conf import settings


SCRIPT_PATH = settings.BASE_DIR / "backupapp" / "scripts" / "backup_core.sh"
RCLONE_CONF_PATH = settings.BASE_DIR / "backupapp" / "scripts" / "rclone.conf"
SCRIPT_VERSION = "2026-03-12 21:00"  # aktualizuj przy zmianach

# ======================================================
# 1. Endpoint INFO – wersja + URL do pobrania
# ======================================================
@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def backup_script_info(request):
    script_url = request.build_absolute_uri(
        reverse('backup-core')
    )

    return JsonResponse({
        "latest_version": SCRIPT_VERSION,
        "script_url": script_url
    })


# ======================================================
# 2. Endpoint pobierania samego pliku
# ======================================================
@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_backup_script(request):
    try:
        with open(SCRIPT_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='text/x-sh')
        response['Content-Type'] = 'text/x-sh; charset=utf-8'
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        return HttpResponse("Script not found", status=404)
    except Exception as e:
        return HttpResponse(f"Server error: {str(e)}", status=500)      
        
def get_rclone_conf(request):
    try:
        with open(RCLONE_CONF_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='text/x-sh')
        response['Content-Type'] = 'text/x-sh; charset=utf-8'
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        return HttpResponse("Script not found", status=404)
    except Exception as e:
        return HttpResponse(f"Server error: {str(e)}", status=500) 
        
def backup_dashboard(request):
    active_invites = BackupInvitation.objects.filter(is_used=False)

    if not active_invites.exists():
        return render(request, 'backup_dashboard.html', {'is_closed': True})

    server_ip = request.get_host().split(':')[0]
    ssh_user = "pi"

    display_data = []

    for invite in active_invites:
        user_token, _ = Token.objects.get_or_create(user=invite.user)

        cmd_packages = "pkg update && pkg upgrade -y && pkg update && pkg upgrade -y && pkg install termux-api rclone openssh exiftool ca-certificates jq -y"

        cmd_keys = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            "if [ ! -f ~/.ssh/id_ed25519 ]; then "
            "ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519; "
            "fi && "
            "chmod 600 ~/.ssh/id_ed25519 && "
            f"ssh-copy-id -o StrictHostKeyChecking=no -i ~/.ssh/id_ed25519.pub {ssh_user}@{server_ip}"
        )

        status_url = request.build_absolute_uri(
            reverse('claim_invitation', args=[invite.token_link])
        )

        script_url = request.build_absolute_uri(
            reverse('backup-core')
        )
        
        rclone_url = request.build_absolute_uri(
            reverse('rclone')
        )

        # TERAZ JEST WEWNĄTRZ PĘTLI
        cmd_final = (
            # f"printf '{user_token.key}' > ~/.backup_token && "
            f"curl -s -L -H 'Authorization: Token {user_token.key}' {rclone_url} > ~/rclone.conf && "
            f"curl -s -L -H 'Authorization: Token {user_token.key}' {script_url} > ~/backup_core.sh && "
            f"chmod +x ~/backup_core.sh && "
            f"curl -s {status_url} > /dev/null && "
            f"echo 'Konfiguracja zakończona sukcesem!'"
        )

        cmd_scheduler = (
            "termux-job-scheduler --cancel --job-id 101 && "
            "termux-job-scheduler --job-id 101 "
            "--script ~/backup_core.sh "
            "--period 3600000 "
            "--persisted true && "
            "echo 'Harmonogram ustawiony na co 1h ale skrypt powinien uruchomić się raz dziennie!'"
        )

        display_data.append({
            'user': invite.user.username,
            'cmd_packages': cmd_packages,
            'cmd_keys': cmd_keys,
            'cmd_final': cmd_final,
            'cmd_scheduler': cmd_scheduler,
        })

    return render(request, 'backup_dashboard.html', {
        'invitations': display_data,
        'is_closed': False
    })

def claim_invitation(request, link_id):
    invitation = get_object_or_404(BackupInvitation, token_link=link_id, is_used=False)
    invitation.is_used = True
    invitation.save()
    return HttpResponse("OK")

@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def backup_report(request):

    user = request.user

    status = request.data.get("status")
    root_space = request.data.get("root_space")
    sd_space = request.data.get("sd_space")
    log_data = request.data.get("log", "")

    agent, created = BackupAgent.objects.get_or_create(user=user)

    agent.last_seen = timezone.now()
    agent.log = log_data
    agent.save()

    return JsonResponse({
        "status": "ok"
    })
