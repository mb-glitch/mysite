from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import base64
import re
import subprocess
import tempfile
from xml.etree import ElementTree

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import User
from django.conf import settings
from django.test import SimpleTestCase, RequestFactory, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from monitoring.models import MonitoredService
from .models import BackupInvitation

from .views import SSH_KEY_INSTALL_COMMAND, backup_dashboard, setup_phone


class DashboardAccessTests(SimpleTestCase):
    def test_anonymous_visitors_are_redirected_from_both_dashboard_urls(self):
        for name in ('backup_dashboard', 'backup_dashboard_alias'):
            with self.subTest(name=name):
                url = reverse(name)
                response = self.client.get(url)
                self.assertRedirects(
                    response, f'{reverse("backup_login")}?next={url}',
                    fetch_redirect_response=False,
                )
                self.assertIn('no-store', response.headers['Cache-Control'])

    def test_login_form_is_public_and_has_csrf_protection(self):
        response = self.client.get(reverse('backup_login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'csrfmiddlewaretoken')

    @patch('backupapp.views.MonitoredService')
    @patch('backupapp.views.BackupInvitation')
    def test_authenticated_user_can_view_dashboard(self, invitations, services):
        invitations.objects.filter.return_value.select_related.return_value.order_by.return_value = []
        services.objects.select_related.return_value.all.return_value.order_by.return_value = []
        request = RequestFactory().get(reverse('backup_dashboard'))
        request.user = SimpleNamespace(is_authenticated=True)
        response = backup_dashboard(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])

    @patch('backupapp.views.MonitoredService')
    @patch('backupapp.views.BackupInvitation')
    def test_dashboard_shows_direct_setup_link_and_local_qr(self, invitations, services):
        invitation = SimpleNamespace(
            user=SimpleNamespace(username='ala'), token_link='test-token',
        )
        invitations.objects.filter.return_value.select_related.return_value.order_by.return_value = [invitation]
        services.objects.select_related.return_value.all.return_value.order_by.return_value = []
        request = RequestFactory().get(reverse('backup_dashboard'))
        request.user = SimpleNamespace(is_authenticated=True)
        html = backup_dashboard(request).content.decode()
        setup_url = request.build_absolute_uri(reverse('setup_phone', args=['test-token']))
        self.assertIn(f'href="{setup_url}"', html)
        self.assertNotIn('Kopiuj Token', html)
        self.assertNotIn('KOPIUJ POLECENIE', html)
        self.assertNotIn('Oznacz jako zużyte', html)
        qr_data = re.search(r'src="data:image/svg\+xml;base64,([^"]+)"', html)
        self.assertIsNotNone(qr_data)
        svg = ElementTree.fromstring(base64.b64decode(qr_data.group(1)))
        self.assertEqual(svg.tag, '{http://www.w3.org/2000/svg}svg')

    @patch('backupapp.views.get_object_or_404')
    def test_installation_link_remains_public(self, token_lookup):
        technical_user = SimpleNamespace(
            username='testuser', is_staff=False, is_superuser=False,
            has_usable_password=lambda: False,
        )
        token_lookup.side_effect = [
            SimpleNamespace(user=technical_user, key='test-token'),
            SimpleNamespace(user=SimpleNamespace(username='testuser'), is_used=False),
            SimpleNamespace(),
        ]
        request = RequestFactory().get('/backupapp/setup/test-token/?raw=1')
        request.user = AnonymousUser()
        response = setup_phone(request, 'test-token')
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertIn(b'#!/data/data/com.termux/files/usr/bin/bash', response.content)
        script = response.content.decode()
        installed_runner = script.split("<< 'RUNNER_EOF'\n", 1)[1].split('\nRUNNER_EOF', 1)[0]
        expected_runner = (Path(__file__).parent / 'scripts/run_backup.sh').read_text().replace(
            'https://host109829.xce.pl/api/monitoring/', settings.URL_MONITORING,
        )
        self.assertEqual(installed_runner, expected_runner)
        syntax = subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        token_write = script.split('# Zapisujemy token konta maszynowego DRF:\n', 1)[1].split('\ncat >', 1)[0]
        ssh_config = script.split("<< 'SSHEOF'\n", 1)[1].split('\nSSHEOF', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / '.token.conf'
            for existing in (False, True):
                with self.subTest(existing_token=existing):
                    if existing:
                        token_file.chmod(0o644)
                    result = subprocess.run(
                        ['bash', '-c', 'set -e; umask 077; BACKUP_DIR="$1"\n' + token_write, 'test', directory],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(token_file.stat().st_mode & 0o777, 0o600)
            config_file = Path(directory) / 'ssh.conf'
            config_file.write_text(ssh_config)
            result = subprocess.run(['ssh', '-G', '-F', str(config_file), 'pi'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('stricthostkeychecking true', result.stdout)
        self.assertNotIn('StrictHostKeyChecking=no', script)
        password_connect = script.index('ssh -F ~/.ssh/config -o StrictHostKeyChecking=ask -o PreferredAuthentications=password,keyboard-interactive')
        host_record_check = script.index(f"ssh-keygen -F '{getattr(settings, 'BACKUP_PI_HOST', '192.168.0.131')}' -f ~/.ssh/known_hosts")
        key_copy = script.index(f"pi '{SSH_KEY_INSTALL_COMMAND}' < \"$HOME/.ssh/id_ed25519.pub\"")
        key_connect = script.index('ssh -F ~/.ssh/config -o BatchMode=yes -o PreferredAuthentications=publickey')
        self.assertLess(password_connect, host_record_check)
        self.assertLess(host_record_check, key_copy)
        self.assertLess(key_copy, key_connect)
        self.assertNotIn('ssh-copy-id', script)
        self.assertIn('(SSH v2)', script)
        self.assertIn('known_hosts_file = ~/.ssh/known_hosts', script)
        self.assertIn('/media/pi/elemele/testuser/.config/backup/scripts', script)
        self.assertIn('/media/pi/elemele/testuser/.config/backup/logs', script)
        self.assertIn('pkg install -y termux-api rclone openssh jq curl util-linux', script)
        self.assertIn('--period-ms 3600000', script)
        self.assertNotIn('--period 3600000', script)
        self.assertIn('if ! termux-job-scheduler --job-id 101', script)
        self.assertIn('BŁĄD: Nie udało się zaplanować backupu', script)

    def test_installing_public_key_is_idempotent_and_sets_ssh_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            key = 'ssh-ed25519 TEST test@phone\n'
            for _ in range(2):
                result = subprocess.run(
                    ['bash', '-c', SSH_KEY_INSTALL_COMMAND], input=key,
                    text=True, capture_output=True, env={'HOME': directory},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            ssh_dir = Path(directory) / '.ssh'
            authorized_keys = ssh_dir / 'authorized_keys'
            self.assertEqual(authorized_keys.read_text().splitlines().count(key.strip()), 1)
            self.assertEqual(ssh_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(authorized_keys.stat().st_mode & 0o777, 0o600)


class BackupInvitationAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'test-password')
        self.owner = User.objects.create_user('maciek')
        self.client.force_login(self.admin)

    def test_new_invitation_provisions_technical_account_and_monitoring_service(self):
        add_url = reverse('admin:backupapp_backupinvitation_add')
        response = self.client.get(add_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['adminform'].form['user'].value(), self.owner.pk)

        response = self.client.post(add_url, {
            'user': self.owner.pk,
            'service_username': 'svc_phone_backup',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation = BackupInvitation.objects.get(user=self.owner)
        account = User.objects.get(username='svc_phone_backup')
        service = MonitoredService.objects.get(service_user=account)
        self.assertEqual(service.owner, self.owner)
        self.assertEqual(service.timeout_ms, 3_600_000)
        self.assertFalse(account.has_usable_password())
        self.assertEqual(invitation.token_link, Token.objects.get(user=account).key)
        self.assertEqual(self.client.get(reverse('setup_phone', args=[invitation.token_link])).status_code, 200)

    def test_existing_orphan_invitation_can_be_linked_to_chosen_owner(self):
        account = User.objects.create_user('krysia', password=None)
        token = Token.objects.create(user=account)
        invitation = BackupInvitation.objects.create(user=account, token_link=token.key, is_used=True)
        url = reverse('setup_phone', args=[token.key])
        self.assertEqual(self.client.get(url).status_code, 404)

        response = self.client.post(reverse('admin:backupapp_backupinvitation_change', args=[invitation.pk]), {
            'user': self.owner.pk,
            'service_username': account.username,
            'is_used': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertEqual(invitation.user, self.owner)
        self.assertEqual(MonitoredService.objects.get(service_user=account).owner, self.owner)
        self.assertTrue(invitation.is_used)
        self.assertEqual(self.client.get(url).status_code, 410)

    def test_setup_link_requires_invitation_and_technical_service(self):
        account = User.objects.create_user('svc_without_invite', password=None)
        token = Token.objects.create(user=account)
        MonitoredService.objects.create(owner=self.owner, service_user=account, service_name='backup_without_invite')
        self.assertEqual(self.client.get(reverse('setup_phone', args=[token.key])).status_code, 404)
        self.assertFalse(BackupInvitation.objects.filter(token_link=token.key).exists())

    def test_admin_rejects_human_account_as_technical_user(self):
        human = User.objects.create_user('human', password='test-password')
        response = self.client.post(reverse('admin:backupapp_backupinvitation_add'), {
            'user': self.owner.pk,
            'service_username': human.username,
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dedykowane konto techniczne bez hasła')
        self.assertFalse(BackupInvitation.objects.exists())
        self.assertFalse(MonitoredService.objects.exists())
