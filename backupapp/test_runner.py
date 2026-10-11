"""Testy jednego skryptu backupu z atrapami sieci i narzędzi Termuxa."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from django.test import SimpleTestCase


class BackupRunnerTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.backup = self.home / 'backup'
        self.backup.mkdir()
        self.bin = self.home / 'bin'
        self.bin.mkdir()
        self.storage = self.home / 'storage'
        (self.storage / 'emulated/0').mkdir(parents=True)
        self.runner = self.backup / 'run_backup.sh'
        shutil.copyfile(Path(__file__).parent / 'scripts/run_backup.sh', self.runner)
        self.remote_runner = self.home / 'remote_runner.sh'
        shutil.copyfile(self.runner, self.remote_runner)
        (self.backup / '.user.conf').write_text('KTO="test"\n')
        (self.backup / '.ssh.conf').write_text('Host pi\n')
        (self.backup / '.rclone-filters.conf').write_text('+ /**\n')
        (self.backup / '.token.conf').write_text('test-token\n')
        self.env = dict(
            os.environ, HOME=str(self.home), BACKUP_STORAGE_ROOT=str(self.storage),
            PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
        )
        for tool in ('ssh', 'termux-wake-lock', 'termux-wake-unlock'):
            self.stub(tool, '#!/bin/bash\nexit 0\n')
        self.stub('termux-notification-remove', '#!/bin/bash\nprintf "remove:%s\\n" "$1" >> "$HOME/notification_calls"\n')
        self.stub('rclone', '''#!/bin/bash
printf '%s\n' "$*" >> "$HOME/rclone_calls"
if [[ "$1" == copyto ]]; then
  if [ "${SYNC_EXIT:-0}" -ne 0 ]; then exit "$SYNC_EXIT"; fi
  cp "$HOME/remote_runner.sh" "$3"
  exit $?
fi
if [[ "$1" == copy && "$2" == "$BACKUP_STORAGE_ROOT/emulated/0" ]]; then
  if [ "${BACKUP_WAIT:-0}" = 1 ]; then
    touch "$HOME/backup_started"
    until [ -f "$HOME/release_backup" ]; do sleep 0.05; done
  fi
  exit "${BACKUP_EXIT:-0}"
fi
if [[ "$1" == copy && "$2" == "$BACKUP_STORAGE_ROOT/card" ]]; then exit "${SD_EXIT:-0}"; fi
exit 0
''')
        self.stub('curl', f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[args.index('-H') + 1] == 'Authorization: Token test-token'
path = args[args.index('--data-binary') + 1][1:]
payload = json.loads(Path(path).read_text())
with (Path(os.environ['HOME']) / 'requests.jsonl').open('a') as out:
    out.write(json.dumps(payload) + '\\n')
print(os.environ.get('HTTP_STATUS', '201'), end='')
sys.exit(int(os.environ.get('CURL_EXIT', '0')))
''')

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def run_backup(self, *args):
        return subprocess.run(['bash', str(self.runner), *args], env=self.env, capture_output=True, timeout=10)

    def reports(self):
        path = self.home / 'requests.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def rclone_calls(self):
        path = self.home / 'rclone_calls'
        return path.read_text().splitlines() if path.exists() else []

    def test_single_script_copies_phone_and_ignores_other_shell_files(self):
        rogue = self.backup / 'another_task.sh'
        rogue.write_text('#!/bin/bash\ntouch "$HOME/rogue_executed"\n')
        legacy = self.backup / '10_backup.sh'
        legacy.write_text('#!/bin/bash\ntouch "$HOME/legacy_executed"\n')
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertFalse((self.home / 'rogue_executed').exists())
        self.assertFalse((self.home / 'legacy_executed').exists())
        self.assertTrue(legacy.exists())
        self.assertEqual(self.reports()[0]['status_code'], 0)
        self.assertTrue((self.backup / '.last_backup_date').exists())
        self.assertTrue(any('copyto pi:/media/pi/elemele/test/.config/backup/scripts/run_backup.sh' in call for call in self.rclone_calls()))
        phone_copies = [call for call in self.rclone_calls() if call.startswith('copy ') and str(self.storage / 'emulated/0') in call]
        self.assertEqual(len(phone_copies), 1)
        self.assertIn('--transfers 4', phone_copies[0])
        self.assertNotIn('--sftp-disable-hashcheck', phone_copies[0])

    def test_existing_phone_switches_back_to_builtin_sftp_before_sync(self):
        (self.backup / '.ssh.conf').write_text(
            'Host pi\n    StrictHostKeyChecking yes\n    UserKnownHostsFile ~/.ssh/known_hosts\n'
        )
        (self.backup / '.rclone.conf').write_text(
            '[pi]\ntype = sftp\nknown_hosts_file = ~/.ssh/known_hosts\n'
            'ssh = ssh -o BatchMode=yes -o StrictHostKeyChecking=yes pi\n'
            '[other]\nssh = custom-ssh\n'
        )
        self.env['RCLONE_SFTP_SSH'] = 'ssh pi'
        self.stub('rclone', '''#!/bin/bash
config="$HOME/.config/rclone/rclone.conf"
if [ "$1" = copyto ]; then
  grep -q '^known_hosts_file = none$' "$config" || exit 21
  grep -q '^ssh = ssh ' "$config" && exit 22
  [ -z "${RCLONE_SFTP_SSH:-}" ] || exit 23
  cp "$HOME/remote_runner.sh" "$3"
fi
exit 0
''')
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertIn('StrictHostKeyChecking no', (self.backup / '.ssh.conf').read_text())
        self.assertIn('UserKnownHostsFile /dev/null', (self.backup / '.ssh.conf').read_text())
        self.assertIn('ssh = custom-ssh', (self.backup / '.rclone.conf').read_text())

    def test_backup_error_reports_actual_exit_code_and_retries_next_time(self):
        self.env['BACKUP_EXIT'] = '23'
        self.assertEqual(self.run_backup().returncode, 1)
        failure = self.reports()[0]['failed_tasks'][0]
        self.assertEqual((failure['script'], failure['exit_code']), ('backup_phone', 23))
        self.assertFalse((self.backup / '.last_backup_date').exists())
        self.env['BACKUP_EXIT'] = '0'
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertTrue((self.backup / '.last_backup_date').exists())

    def test_sd_card_error_is_reported(self):
        (self.storage / 'card').mkdir()
        (self.backup / '.user.conf').write_text('KTO="test"\nSD_CARD="card"\n')
        self.env['SD_EXIT'] = '42'
        self.assertEqual(self.run_backup().returncode, 1)
        failure = self.reports()[0]['failed_tasks'][0]
        self.assertEqual((failure['script'], failure['exit_code']), ('backup_sd', 42))

    def test_missing_filters_is_error_and_skips_copy(self):
        (self.backup / '.rclone-filters.conf').unlink()
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['script'], 'configuration')
        self.assertFalse(any(str(self.storage / 'emulated/0') in call for call in self.rclone_calls()))

    def test_failed_delivery_retried_before_daily_lock(self):
        self.env['HTTP_STATUS'] = '503'
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(len(list((self.backup / '.monitoring-pending').glob('*.json'))), 1)
        self.env['HTTP_STATUS'] = '201'
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertEqual(len(self.reports()), 2)
        self.assertEqual(list((self.backup / '.monitoring-pending').glob('*.json')), [])
        self.assertEqual(sum(str(self.storage / 'emulated/0') in call for call in self.rclone_calls()), 1)

    def test_rclone_does_not_need_external_ssh_support(self):
        self.stub('rclone', '#!/bin/bash\nexit 0\n')
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['script'], 'sync_script')

    def test_sync_error_does_not_hide_successful_copy(self):
        self.env['SYNC_EXIT'] = '12'
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['exit_code'], 12)
        self.assertTrue(any(str(self.storage / 'emulated/0') in call for call in self.rclone_calls()))
        self.assertFalse((self.backup / '.last_backup_date').exists())

    def test_incompatible_remote_script_is_not_installed(self):
        self.remote_runner.write_text('#!/bin/bash\nexit 0\n')
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertIn('# BACKUP_SINGLE_TASK_V1', self.runner.read_text())
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['script'], 'sync_script')

    def test_autoupdate_restarts_once_and_keeps_lock(self):
        self.remote_runner.write_text(self.remote_runner.read_text() + '\n# aktualizacja\n')
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertIn('# aktualizacja', self.runner.read_text())
        self.assertEqual(len(self.reports()), 1)
        self.assertEqual(self.runner.stat().st_mode & 0o777, 0o700)
        self.assertEqual(sum(call.startswith('copyto ') for call in self.rclone_calls()), 1)

    def test_daily_backup_skips_pi_and_update_on_later_runs(self):
        self.assertEqual(self.run_backup().returncode, 0)
        calls_after_backup = self.rclone_calls()
        self.stub('ssh', '#!/bin/bash\ntouch "$HOME/ssh_contacted"\nexit 255\n')
        self.remote_runner.write_text(self.remote_runner.read_text() + '\n# nowa wersja\n')
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertNotIn('# nowa wersja', self.runner.read_text())
        self.assertFalse((self.home / 'ssh_contacted').exists())
        self.assertEqual(self.rclone_calls(), calls_after_backup)
        self.assertEqual(len(self.reports()), 1)

    def test_force_updates_and_backs_up_again_after_daily_backup(self):
        self.assertEqual(self.run_backup().returncode, 0)
        self.remote_runner.write_text(self.remote_runner.read_text() + '\n# nowa wersja\n')
        self.assertEqual(self.run_backup('--force').returncode, 0)
        self.assertIn('# nowa wersja', self.runner.read_text())
        self.assertEqual(len(self.reports()), 2)
        self.assertEqual(sum(str(self.storage / 'emulated/0') in call for call in self.rclone_calls()), 2)

    def test_unavailable_pi_skips_backup_without_success_report(self):
        self.stub('ssh', '#!/bin/bash\nexit 255\n')
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertEqual(self.rclone_calls(), [])
        self.assertEqual(self.reports(), [])
        self.assertFalse((self.backup / '.last_backup_date').exists())

    def test_second_runner_skips_while_first_is_copying(self):
        self.remote_runner.write_text(self.remote_runner.read_text() + '\n# aktualizacja przed kopią\n')
        self.env['BACKUP_WAIT'] = '1'
        first = subprocess.Popen(['bash', str(self.runner)], env=self.env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not (self.home / 'backup_started').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.home / 'backup_started').exists())
            self.assertIsNone(first.poll())
            self.assertEqual(self.run_backup().returncode, 0)
            self.assertEqual(self.reports(), [])
            self.assertIn('Inna instancja run_backup.sh', (self.backup / 'logs/backup.log').read_text())
        finally:
            (self.home / 'release_backup').touch()
            first.wait(timeout=5)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(len(self.reports()), 1)

    def test_remote_directory_error_is_reported(self):
        self.stub('ssh', '#!/bin/bash\nif [ "${@: -1}" = true ]; then exit 0; fi\nexit 13\n')
        self.assertEqual(self.run_backup().returncode, 1)
        failure = self.reports()[0]['failed_tasks'][0]
        self.assertEqual((failure['script'], failure['exit_code']), ('prepare_remote', 13))
        self.assertFalse(any(str(self.storage / 'emulated/0') in call for call in self.rclone_calls()))

    def test_backup_destination_error_reports_ssh_exit_code(self):
        self.stub('ssh', '#!/bin/bash\n[[ "${@: -1}" == *"_backup_tel_sync"* ]] && exit 13\nexit 0\n')
        self.assertEqual(self.run_backup().returncode, 1)
        failure = self.reports()[0]['failed_tasks'][0]
        self.assertEqual((failure['script'], failure['exit_code']), ('backup_destination', 13))
        self.assertFalse(any(str(self.storage / 'emulated/0') in call for call in self.rclone_calls()))
