from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.authtoken.models import Token
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from pathlib import Path

from .models import BackupInvitation, BackupAgent

# Ścieżki na serwerze Django do plików konfiguracyjnych
SCRIPT_DIR = getattr(settings, 'BASE_DIR', Path('.')) / "backupapp" / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "10_backup.sh"
RCLONE_CONF_PATH = SCRIPT_DIR / "rclone.conf"
SCRIPT_VERSION = "2026-09-22 14:00"


# ==============================================================================
# 1. Endpoint INFO – wersja i URL do pobrania
# ==============================================================================
@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def backup_script_info(request):
    script_url = request.build_absolute_uri(reverse('backup-core'))
    return JsonResponse({
        "latest_version": SCRIPT_VERSION,
        "script_url": script_url
    })


# ==============================================================================
# 2. Endpoint pobierania skryptu backupu
# ==============================================================================
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
    except Exception as e:
        return HttpResponse(f"Server error: {str(e)}", status=500)


# ==============================================================================
# 3. Endpoint pobierania konfiguracji Rclone
# ==============================================================================
def get_rclone_conf(request):
    try:
        with open(RCLONE_CONF_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        response = HttpResponse(content, content_type='text/x-sh; charset=utf-8')
        response['Cache-Control'] = 'no-cache'
        return response
    except FileNotFoundError:
        return HttpResponse("rclone.conf not found", status=404)
    except Exception as e:
        return HttpResponse(f"Server error: {str(e)}", status=500)


# ==============================================================================
# 4. Bezpośrednia instalacja (curl -sL https://.../setup/<token>/ | bash LUB przeglądarka)
# ==============================================================================
def setup_phone(request, token):
    """
    Jeśli wywołane z curl/wget w Termuxie: zwraca czysty skrypt bash.
    Jeśli otwarte w przeglądarce telefonu: wyświetla minimalistyczną stronę z 1 przyciskiem kopiowania.
    Wyszukiwanie bezpośrednio po Tokenie autoryzacyjnym użytkownika.
    """
    token_obj = get_object_or_404(Token, key=token)
    user = token_obj.user
    # Nazwy użytkowników konsekwentnie z małej litery (np. maciek, krysia, asia, zuzia)
    username = user.username.lower()

    # Zapewnienie istnienia rekordu statusu zaproszenia
    invitation, _ = BackupInvitation.objects.get_or_create(
        user=user,
        defaults={'token_link': token}
    )

    server_ip = '192.168.0.131'
    ssh_user = 'smerf'
    status_url = request.build_absolute_uri(reverse('claim_invitation', args=[token]))
    script_url = request.build_absolute_uri(reverse('setup_phone', args=[token]))

    # Czysty skrypt instalacyjny generowany w locie
    bash_script = f"""#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "=========================================================="
echo "  Instalacja backupu telefonu dla: {username}"
echo "=========================================================="

echo "[1/6] Sprawdzanie i instalacja pakietów w Termuxie..."
export DEBIAN_FRONTEND=noninteractive
pkg install -y -o Dpkg::Options::="--force-confold" termux-api rclone openssh jq curl </dev/null 2>/dev/null || pkg install -y termux-api rclone openssh jq curl </dev/null

echo "[2/6] Dostęp do pamięci Androida..."
if [ -d "$HOME/storage" ] && [ -d "$HOME/storage/shared" ]; then
  echo "-> Uprawnienia do pamięci telefonu są już aktywne ($HOME/storage istnieje)."
else
  echo "-> Wywoływanie uprawnień do pamięci..."
  termux-setup-storage </dev/null 2>/dev/null || true
  echo "-> Jeśli na ekranie telefonu pojawiło się okienko Androida, kliknij 'Zezwól'."
  echo "-> Czekam 5 sekund na zatwierdzenie uprawnień w systemie..."
  sleep 5
fi

# Wykrywanie karty SD (3 niezawodne metody bez potrzeby uprawnień root)
SD_DETECTED=""
if [ -f /proc/mounts ]; then
  SD_DETECTED=$(grep -oE '/storage/[0-9A-Za-z_-]+' /proc/mounts 2>/dev/null | grep -vE '/storage/(emulated|self)' | head -n1 || true)
fi
if [ -z "$SD_DETECTED" ]; then
  SD_DETECTED=$(df 2>/dev/null | grep -oE '/storage/[0-9A-Za-z_-]+' | grep -vE '/storage/(emulated|self)' | head -n1 || true)
fi
if [ -z "$SD_DETECTED" ] && [ -e "$HOME/storage/external-1" ]; then
  EXT_TARGET=$(readlink -f "$HOME/storage/external-1" 2>/dev/null || true)
  SD_DETECTED=$(echo "$EXT_TARGET" | grep -oE '/storage/[^/]+' | grep -vE '/storage/(emulated|self)' | head -n1 || true)
fi

SD_CARD=""
if [ -n "$SD_DETECTED" ]; then
  SD_CARD=$(basename "$SD_DETECTED")
  echo "-> Wykryto kartę SD: $SD_CARD (/storage/$SD_CARD)"
else
  echo "-> Brak dodatkowej karty SD (tylko pamięć główna)."
fi

echo "[3/6] Konfiguracja klucza SSH..."
mkdir -p ~/.ssh && chmod 700 ~/.ssh
if [ ! -f ~/.ssh/id_ed25519 ]; then
  ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519 </dev/null
fi
chmod 600 ~/.ssh/id_ed25519

# Wpis w ~/.ssh/config dla 'pi'
if grep -q "Host pi" ~/.ssh/config 2>/dev/null; then
  sed -i '/Host pi/,/StrictHostKeyChecking/d' ~/.ssh/config 2>/dev/null || true
fi

cat >> ~/.ssh/config << 'SSHEOF'

Host pi
    User {ssh_user}
    HostName {server_ip}
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
SSHEOF
chmod 600 ~/.ssh/config

echo "-> Podaj JEDNORAZOWO hasło do Raspberry Pi ({ssh_user}@{server_ip}):"
ssh-copy-id -o StrictHostKeyChecking=no -i ~/.ssh/id_ed25519.pub {ssh_user}@{server_ip} < /dev/tty || ssh-copy-id -o StrictHostKeyChecking=no -i ~/.ssh/id_ed25519.pub {ssh_user}@{server_ip}

# Tworzenie katalogów na Pi dla tego użytkownika (z małej litery)
ssh {ssh_user}@{server_ip} "mkdir -p '/media/pi/elemele/{username}/_backup_tel_sync' '/media/pi/elemele/{username}/.skrypty'" < /dev/null

echo "[4/6] Tworzenie głównego katalogu ~/backup na telefonie i konfiguracja..."
BACKUP_DIR="$HOME/backup"
mkdir -p "$BACKUP_DIR" ~/.config/rclone
cd "$BACKUP_DIR"

cat > "$BACKUP_DIR/.backup_user" << USREOF
KTO="{username}"
SD_CARD="$SD_CARD"
PI_USER="{ssh_user}"
DEST_BASE="/media/pi/elemele"
USREOF

echo '{token}' > "$BACKUP_DIR/.backup_token"

cat > ~/.config/rclone/rclone.conf << 'EOF'
[pi]
type = sftp
host = {server_ip}
user = {ssh_user}
port = 22
key_file = ~/.ssh/id_ed25519
shell_type = unix
EOF
chmod 600 ~/.config/rclone/rclone.conf

# Filtry wykluczające cache, miniatury i zbędne pliki
cat > "$BACKUP_DIR/.rclone-filters.txt" << 'EOF'
+ /DCIM/**
+ /Pictures/**
+ /Documents/**
+ /Download/**
+ /Music/**
+ /Android/media/**
- **.tmp
- **.swp
- **.bak
- **.nomedia
- **.thumbnails/**
- **Cache/**
- **cache/**
- **Thumbs.db
- **.DS_Store
- *
EOF

echo "[5/6] Instalacja dyspozytora $BACKUP_DIR/run_backup.sh..."
cat > "$BACKUP_DIR/run_backup.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -u

BACKUP_DIR="$HOME/backup"
cd "$BACKUP_DIR"

LOCK_FILE="$BACKUP_DIR/.last_backup_date"
TODAY=$(date +'%F')
USER_FILE="$BACKUP_DIR/.backup_user"

# 1. Odczytaj nazwę użytkownika (kto) i upewnij się, że jest z małej litery
if [ ! -f "$USER_FILE" ]; then
  echo "Brak pliku $USER_FILE!"
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

# 2. Blokada dzienna (jeśli dzisiaj backup już się wykonał - wyjdź natychmiast)
if [ "${{1:-}}" != "--force" ] && [ -f "$LOCK_FILE" ] && [ "$(cat "$LOCK_FILE" 2>/dev/null)" = "$TODAY" ]; then
  exit 0
fi

# 3. Sprawdź obecność w domowym Wi-Fi (timeout 2s)
if ! ssh -o ConnectTimeout=2 -o BatchMode=yes -o StrictHostKeyChecking=no {ssh_user}@{server_ip} "true" 2>/dev/null; then
  exit 0
fi

termux-wake-lock 2>/dev/null || true
trap 'termux-wake-unlock 2>/dev/null || true' EXIT

# 4. Synchronizacja indywidualnego katalogu .skrypty dla tego użytkownika z Raspberry Pi
REMOTE_PATH="pi:/media/pi/elemele/$KTO/.skrypty"

rclone copy "$REMOTE_PATH" "$BACKUP_DIR" --fast-list -q 2>/dev/null || true

# Jeśli w .skrypty na Pi znajduje się zaktualizowany rclone.conf, zaktualizuj go na telefonie
if [ -f "$BACKUP_DIR/rclone.conf" ]; then
  cp "$BACKUP_DIR/rclone.conf" "$HOME/.config/rclone/rclone.conf"
  chmod 600 "$HOME/.config/rclone/rclone.conf"
fi

chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

# 5. Uruchomienie alfabetycznie wszystkich zadań *.sh (np. 10_backup.sh)
ALL_OK=true
for script in $(ls "$BACKUP_DIR"/*.sh 2>/dev/null | sort); do
  [ -f "$script" ] || continue
  # Pomijamy samego runnera
  [ "$(basename "$script")" = "run_backup.sh" ] && continue

  if ! bash "$script" "$@"; then
    ALL_OK=false
  fi
done

if [ "$ALL_OK" = true ]; then
  echo "$TODAY" > "$LOCK_FILE"
fi
EOF
chmod +x "$BACKUP_DIR/run_backup.sh"

# Pierwsza synchronizacja z dedykowanego katalogu użytkownika na Pi
rclone copy "pi:/media/pi/elemele/{username}/.skrypty" "$BACKUP_DIR" -q 2>/dev/null || true
if [ -f "$BACKUP_DIR/rclone.conf" ]; then
  cp "$BACKUP_DIR/rclone.conf" ~/.config/rclone/rclone.conf
  chmod 600 ~/.config/rclone/rclone.conf
fi
chmod +x "$BACKUP_DIR"/*.sh 2>/dev/null || true

echo "[6/6] Ustawianie automatycznego harmonogramu (co 1h na Wi-Fi)..."
termux-job-scheduler --cancel --job-id 101 2>/dev/null || true
termux-job-scheduler --job-id 101 --script "$BACKUP_DIR/run_backup.sh" --period 3600000 --network unmetered --persisted true

# Oznaczenie tokenu jako skonfigurowany
curl -s "{status_url}" > /dev/null 2>&1 || true

# Posprzątanie tymczasowego instalatora
rm -f "$HOME/setup.sh" 2>/dev/null || true

echo ""
echo "=========================================================="
echo "  GOTOWE! Backup dla {username} skonfigurowany w ~/backup."
echo "=========================================================="
"""

    # Jeśli wywołanie pochodzi z curl lub wget w Termuxie:
    user_agent = request.headers.get('User-Agent', '').lower()
    if 'curl' in user_agent or 'wget' in user_agent or request.GET.get('raw'):
        return HttpResponse(bash_script, content_type='text/plain; charset=utf-8')

    # Jeśli otwarto w przeglądarce telefonu:
    one_liner = f"curl -sL {script_url} -o ~/setup.sh && bash ~/setup.sh"
    return render(request, 'setup.html', {
        'user': user,
        'token': token,
        'one_liner': one_liner,
        'direct_url': script_url,
    })


# ==============================================================================
# 5. Panel Dashboardu Onboardingu (TYLKO aktywne zaproszenia do wykorzystania)
# ==============================================================================
def backup_dashboard(request):
    """
    Wyświetla TYLKO aktywne (jeszcze nieużyte) zaproszenia do instalacji backupu.
    Gdy wszystkie telefony zostaną skonfigurowane (is_used=True), lista jest pusta!
    """
    active_invites = BackupInvitation.objects.filter(is_used=False).select_related('user').order_by('user__username')

    display_data = []

    for invite in active_invites:
        token_obj, _ = Token.objects.get_or_create(user=invite.user)
        user_name = invite.user.username.lower()
        token_key = token_obj.key

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
    })


# ==============================================================================
# 5. Oznaczenie zaproszenia jako zużyte
# ==============================================================================
def claim_invitation(request, token):
    """
    Oznacza zaproszenie jako zużyte (is_used=True).
    Może być wywołane:
    1. Automatycznie przez skrypt bash w Termuxie na końcu instalacji.
    2. Ręcznie przez administratora jednym kliknięciem z Dashboardu (odświeża stronę).
    """
    token_obj = get_object_or_404(Token, key=token)
    invitation = BackupInvitation.objects.filter(user=token_obj.user).first()
    if invitation:
        invitation.is_used = True
        invitation.save()

    # Jeśli kliknięto w przeglądarce, przekieruj z powrotem na Dashboard
    if request.GET.get('redirect') or ('text/html' in request.headers.get('Accept', '')):
        from django.shortcuts import redirect
        return redirect('backup_dashboard')

    return HttpResponse("OK")


# ==============================================================================
# 6. API Raportowania / Monitoring
# ==============================================================================
@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def backup_report(request):
    user = request.user
    status_code = request.data.get("status_code", 0)
    message = request.data.get("message", {})

    agent, _ = BackupAgent.objects.get_or_create(user=user)
    agent.last_seen = timezone.now()
    agent.last_status_code = status_code

    if isinstance(message, dict) and "error" in message:
        agent.log = f"[{timezone.now().strftime('%Y-%m-%d %H:%M:%S')}] BŁĄD: {message['error']}"
    else:
        agent.log = f"[{timezone.now().strftime('%Y-%m-%d %H:%M:%S')}] Sukces (status 0)"

    agent.save()

    return JsonResponse({"status": "ok"})

