from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import base64
import hashlib
import re
import subprocess
import tempfile
from xml.etree import ElementTree

from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.conf import settings
from django.test import SimpleTestCase, RequestFactory, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from monitoring.models import LogEntry, MonitoredService
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
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=False, pk=999)
        response = backup_dashboard(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])

    @patch('backupapp.views.MonitoredService')
    @patch('backupapp.views.BackupInvitation')
    def test_dashboard_shows_direct_setup_link_and_local_qr(self, invitations, services):
        invitation = SimpleNamespace(
            pk=1, user_id=1, user=SimpleNamespace(username='ala'), token_link='test-token',
        )
        invitations.objects.filter.return_value.select_related.return_value.order_by.return_value = [invitation]
        services.objects.select_related.return_value.all.return_value.order_by.return_value = []
        request = RequestFactory().get(reverse('backup_dashboard'))
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=False, pk=999)
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
            self.assertIn('stricthostkeychecking false', result.stdout)
            self.assertIn('userknownhostsfile /dev/null', result.stdout)
        password_connect = script.index('ssh -F ~/.ssh/config -o PreferredAuthentications=password,keyboard-interactive')
        key_copy = script.index(f"pi '{SSH_KEY_INSTALL_COMMAND}' < \"$HOME/.ssh/id_ed25519.pub\"")
        key_connect = script.index('ssh -F ~/.ssh/config -o BatchMode=yes -o PreferredAuthentications=publickey')
        self.assertLess(password_connect, key_copy)
        self.assertLess(key_copy, key_connect)
        self.assertEqual(script.count('PreferredAuthentications=password,keyboard-interactive'), 1)
        self.assertNotIn('ssh-copy-id', script)
        self.assertIn('(SSH v2)', script)
        self.assertIn('known_hosts_file = none', script)
        self.assertNotIn('ssh = ssh -o BatchMode=yes', script)
        self.assertIn('unset RCLONE_SFTP_SSH', installed_runner)
        self.assertIn('/media/pi/elemele/testuser/.config/backup/scripts', script)
        self.assertIn('/media/pi/elemele/testuser/.config/backup/logs', script)
        self.assertIn('pkg install -y termux-api rclone openssh jq curl util-linux', script)
        installer_tail = script.split('\nRUNNER_EOF\n', 1)[1]
        self.assertIn('rclone copyto "$BACKUP_DIR/run_backup.sh" "pi:$REMOTE_RUNNER.next" -q', installer_tail)
        self.assertIn("bash -n '$REMOTE_RUNNER.next' && chmod 700", installer_tail)
        self.assertNotIn("grep -qFx '# BACKUP_SINGLE_TASK_V1'", installer_tail)
        self.assertIn('--period-ms 3600000', script)
        self.assertNotIn('--period 3600000', script)
        self.assertIn('if ! termux-job-scheduler --job-id 101', script)
        self.assertIn('BŁĄD: Nie udało się zaplanować backupu', script)

    def test_backup_uses_one_script_and_builtin_sftp_for_rclone(self):
        scripts_dir = Path(__file__).parent / 'scripts'
        self.assertFalse((scripts_dir / '10_backup.sh').exists())
        script = (scripts_dir / 'run_backup.sh').read_text()
        syntax = subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn('unset RCLONE_SFTP_SSH', script)
        self.assertNotIn('--sftp-disable-hashcheck', script)

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

    def test_selected_app_user_sets_kto_while_maciek_owns_new_service(self):
        app_user = User.objects.create_user('krysia', password='test-password')
        response = self.client.post(reverse('admin:backupapp_backupinvitation_add'), {
            'user': app_user.pk, 'service_username': 'svc_krysia_phone', '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation = BackupInvitation.objects.get(user=app_user)
        service = MonitoredService.objects.get(service_user=invitation.technical_user)
        self.assertEqual(service.owner, self.owner)
        self.assertEqual(invitation.user, app_user)
        installer = self.client.get(reverse('setup_phone', args=[invitation.token_link]), {'raw': '1'})
        self.assertEqual(installer.status_code, 200)
        self.assertContains(installer, 'KTO="krysia"')
        self.assertContains(installer, '/media/pi/elemele/krysia/_backup_tel_sync')
        self.assertNotContains(installer, 'KTO="maciek"')

    def test_model_creation_uses_same_default_owner_and_hourly_interval(self):
        app_user = User.objects.create_user('krysia', password='test-password')
        invitation = BackupInvitation.objects.create(user=app_user)
        service = MonitoredService.objects.get(service_user=invitation.technical_user)
        self.assertEqual(invitation.technical_user.username, 'svc_krysia_backup')
        self.assertEqual(service.owner, self.owner)
        self.assertEqual(service.timeout_ms, 3_600_000)
        self.assertContains(self.client.get(reverse('setup_phone', args=[invitation.token_link]), {'raw': '1'}), 'KTO="krysia"')

    def test_editing_invitation_preserves_existing_service_owner_token_and_interval(self):
        app_user = User.objects.create_user('krysia', password='test-password')
        invitation = BackupInvitation.objects.create(user=app_user)
        service = MonitoredService.objects.get(service_user=invitation.technical_user)
        other_owner = User.objects.create_user('other_owner', password='test-password')
        service.owner = other_owner
        service.timeout_ms = 86_400_000
        service.save()
        old_token = invitation.token_link
        response = self.client.post(reverse('admin:backupapp_backupinvitation_change', args=[invitation.pk]), {
            'user': app_user.pk, 'service_username': invitation.technical_user.username, '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        invitation.refresh_from_db()
        self.assertEqual(service.owner, other_owner)
        self.assertEqual(service.timeout_ms, 86_400_000)
        self.assertEqual(invitation.token_link, old_token)
        self.assertContains(self.client.get(reverse('setup_phone', args=[old_token]), {'raw': '1'}), 'KTO="krysia"')

    def test_missing_maciek_does_not_assign_service_to_another_user(self):
        self.owner.delete()
        app_user = User.objects.create_user('krysia', password='test-password')
        response = self.client.post(reverse('admin:backupapp_backupinvitation_add'), {
            'user': app_user.pk, 'service_username': 'svc_krysia_phone', '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Brak użytkownika maciek')
        self.assertFalse(BackupInvitation.objects.exists())
        self.assertFalse(MonitoredService.objects.exists())
        self.assertFalse(User.objects.filter(username='svc_krysia_phone').exists())
        with self.assertRaises(ValidationError):
            BackupInvitation.objects.create(user=app_user)
        self.assertFalse(User.objects.filter(username='svc_krysia_backup').exists())

    def test_script_api_reports_hash_of_current_installer_script(self):
        invitation = BackupInvitation.objects.create(user=self.owner)
        authorization = {'HTTP_AUTHORIZATION': f'Token {invitation.token_link}'}
        response = self.client.get(reverse('backup-core'), **authorization)
        self.assertEqual(response.status_code, 200)
        self.assertIn('BACKUP_SINGLE_TASK_V1', response.content.decode())
        info = self.client.get(reverse('backup-info'), **authorization)
        self.assertEqual(info.status_code, 200)
        self.assertEqual(info.json()['latest_version'], hashlib.sha256(response.content).hexdigest())

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

    def test_legacy_invitation_can_be_relinked_to_new_technical_account(self):
        human = User.objects.create_user('krysia', password='test-password')
        old_token = Token.objects.create(user=human)
        invitation = BackupInvitation.objects.create(
            user=human, token_link=old_token.key, is_used=True,
        )
        change_url = reverse('admin:backupapp_backupinvitation_change', args=[invitation.pk])
        response = self.client.get(change_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['adminform'].form['service_username'].value())

        response = self.client.post(change_url, {
            'user': self.owner.pk,
            'service_username': 'svc_krysia_phone',
            'is_used': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        account = User.objects.get(username='svc_krysia_phone')
        self.assertEqual(invitation.user, self.owner)
        self.assertEqual(invitation.token_link, Token.objects.get(user=account).key)
        self.assertFalse(invitation.is_used)
        self.assertFalse(account.has_usable_password())
        self.assertEqual(MonitoredService.objects.get(service_user=account).owner, self.owner)
        self.assertTrue(User.objects.get(pk=human.pk).has_usable_password())
        self.assertTrue(Token.objects.filter(pk=old_token.pk).exists())
        self.assertEqual(self.client.get(reverse('setup_phone', args=[old_token.key])).status_code, 404)
        self.assertEqual(self.client.get(reverse('setup_phone', args=[invitation.token_link])).status_code, 200)

    def test_existing_technical_invitation_cannot_be_relinked(self):
        invitation = BackupInvitation.objects.create(user=self.owner)
        old_token_link = invitation.token_link
        response = self.client.post(reverse('admin:backupapp_backupinvitation_change', args=[invitation.pk]), {
            'user': self.owner.pk,
            'service_username': 'svc_other_phone',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Konta technicznego istniejącego zaproszenia nie można zmienić.')
        invitation.refresh_from_db()
        self.assertEqual(invitation.token_link, old_token_link)
        self.assertFalse(User.objects.filter(username='svc_other_phone').exists())

    def test_archived_invitation_is_hidden_and_can_be_restored(self):
        invitation = BackupInvitation.objects.create(user=self.owner)
        account = Token.objects.get(key=invitation.token_link).user
        change_url = reverse('admin:backupapp_backupinvitation_change', args=[invitation.pk])
        response = self.client.post(change_url, {
            'user': self.owner.pk,
            'service_username': account.username,
            'is_archived': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertTrue(invitation.is_archived)
        invitation_list = reverse('admin:backupapp_backupinvitation_changelist')
        self.assertEqual(self.client.get(invitation_list).context['cl'].result_count, 0)
        self.assertEqual(self.client.get(invitation_list, {'archive': 'archived'}).context['cl'].result_count, 1)
        self.assertEqual(self.client.get(invitation_list, {'archive': 'all'}).context['cl'].result_count, 1)
        self.assertEqual(self.client.get(reverse('backup_dashboard')).context['invitations'], [])
        setup_url = reverse('setup_phone', args=[invitation.token_link])
        self.assertEqual(self.client.get(setup_url).status_code, 404)

        response = self.client.post(change_url, {
            'user': self.owner.pk,
            'service_username': account.username,
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertFalse(invitation.is_archived)
        self.assertEqual(self.client.get(invitation_list).context['cl'].result_count, 1)
        self.assertEqual(self.client.get(setup_url).status_code, 200)

    def test_legacy_personal_account_invitation_can_be_archived_without_rebinding(self):
        human = User.objects.create_user('krysia', password='test-password')
        token = Token.objects.create(user=human)
        invitation = BackupInvitation.objects.create(user=human, token_link=token.key)
        response = self.client.post(reverse('admin:backupapp_backupinvitation_change', args=[invitation.pk]), {
            'user': human.pk,
            'service_username': '',
            'is_archived': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertTrue(invitation.is_archived)
        self.assertEqual(invitation.token_link, token.key)
        self.assertTrue(human.has_usable_password())
        self.assertFalse(MonitoredService.objects.filter(service_user=human).exists())


class BackupDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'test-password')
        self.owner = User.objects.create_user('maciek')
        self.client.force_login(self.admin)
        self.invitation = BackupInvitation.objects.create(user=self.owner)
        self.account = self.invitation.technical_user
        self.token = Token.objects.get(user=self.account)
        self.service = MonitoredService.objects.get(service_user=self.account)
        self.log = LogEntry.objects.create(service=self.service)

    def assert_backup_deleted(self):
        self.assertFalse(BackupInvitation.objects.filter(pk=self.invitation.pk).exists())
        self.assertFalse(MonitoredService.objects.filter(pk=self.service.pk).exists())
        self.assertFalse(LogEntry.objects.filter(pk=self.log.pk).exists())
        self.assertFalse(Token.objects.filter(pk=self.token.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.account.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_deleting_invitation_in_admin_removes_complete_backup(self):
        url = reverse('admin:backupapp_backupinvitation_delete', args=[self.invitation.pk])
        response = self.client.get(url)
        self.assertContains(response, f'Konto techniczne: {self.account.username}')
        self.assertContains(response, f'Usługa monitoringu: {self.service.service_name}')
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assert_backup_deleted()

    def test_deleting_service_in_admin_removes_complete_backup(self):
        url = reverse('admin:monitoring_monitoredservice_delete', args=[self.service.pk])
        response = self.client.get(url)
        self.assertContains(response, f'Konto techniczne: {self.account.username}')
        self.assertContains(response, f'Zaproszenie do instalacji: {self.invitation.pk}')
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assert_backup_deleted()

    def test_bulk_deletion_uses_same_cascade(self):
        list_url = reverse('admin:backupapp_backupinvitation_changelist')
        payload = {'action': 'delete_selected', '_selected_action': [self.invitation.pk], 'post': 'yes'}
        self.assertEqual(self.client.post(list_url, payload).status_code, 302)
        self.assert_backup_deleted()

    def test_deleting_service_model_removes_complete_backup(self):
        self.service.delete()
        self.assert_backup_deleted()

    def test_legacy_personal_account_is_preserved(self):
        human = User.objects.create_user('krysia', password='password')
        token = Token.objects.create(user=human)
        invitation = BackupInvitation.objects.create(user=human, token_link=token.key)
        self.assertIsNone(invitation.technical_user)
        invitation.delete()
        self.assertTrue(User.objects.filter(pk=human.pk).exists())
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())
