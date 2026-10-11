from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import subprocess
import tempfile

from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from django.test import SimpleTestCase, RequestFactory
from django.urls import reverse

from .views import backup_dashboard, setup_phone


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

    @patch('backupapp.views.get_object_or_404')
    @patch('backupapp.views.BackupInvitation')
    def test_installation_link_remains_public(self, invitations, token_lookup):
        invitations.objects.filter.return_value.first.return_value = SimpleNamespace(
            user=SimpleNamespace(username='testuser'), is_used=False,
        )
        request = RequestFactory().get('/backupapp/setup/test-token/?raw=1')
        request.user = AnonymousUser()
        response = setup_phone(request, 'test-token')
        self.assertEqual(response.status_code, 200)
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
        self.assertIn('known_hosts_file = ~/.ssh/known_hosts', script)
        self.assertIn('/media/pi/elemele/testuser/.config/backup/scripts', script)
        self.assertIn('/media/pi/elemele/testuser/.config/backup/logs', script)
