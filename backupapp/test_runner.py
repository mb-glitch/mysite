"""Testy rzeczywistego runnera z atrapami sieci i narzędzi Termux."""
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
        shutil.copyfile(Path(__file__).parent / 'scripts/run_backup.sh', self.backup / 'run_backup.sh')
        (self.backup / '.user.conf').write_text('KTO="test"\n')
        (self.backup / '.ssh.conf').write_text('Host pi\n')
        (self.backup / '.token.conf').write_text('test-token\n')
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        for tool in ('ssh', 'termux-wake-lock', 'termux-wake-unlock'):
            self.stub(tool, '#!/bin/bash\nexit 0\n')
        self.stub('termux-notification', '#!/bin/bash\nprintf "create\\n" >> "$HOME/notification_calls"\n')
        self.stub('termux-notification-remove', '#!/bin/bash\nprintf "remove:%s\\n" "$1" >> "$HOME/notification_calls"\n')
        self.stub('rclone', '#!/bin/bash\nif [[ "$*" == *"/.config/backup/scripts"* ]]; then exit "${SYNC_EXIT:-0}"; fi\nexit 0\n')
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

    def task(self, name, code):
        (self.backup / name).write_text(f'#!/bin/bash\nexit {code}\n')

    def run_backup(self, *args):
        return subprocess.run(['bash', str(self.backup / 'run_backup.sh'), *args], env=self.env, capture_output=True, timeout=10)

    def reports(self):
        path = self.home / 'requests.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_task_error_contains_name_and_actual_exit_code(self):
        name = '20 backup "błąd" \\ plik.sh'
        self.task(name, 23)
        self.task('10 success.sh', 0)
        self.assertEqual(self.run_backup().returncode, 1)
        payload = self.reports()[0]
        self.assertEqual(payload['status_code'], 2)
        self.assertEqual(payload['failed_tasks'][0]['script'], name)
        self.assertEqual(payload['failed_tasks'][0]['exit_code'], 23)
        self.assertIn('kod: 23', payload['error'])
        self.assertTrue((self.backup / '.locks/10 success.sh.done').exists())
        self.assertFalse((self.backup / f'.locks/{name}.done').exists())
        self.assertFalse((self.backup / '.last_backup_date').exists())

    def test_runner_only_removes_old_local_notification(self):
        self.task('10 success.sh', 0)
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertEqual((self.home / 'notification_calls').read_text().splitlines(), ['remove:999'])
        self.task('20 failure.sh', 7)
        self.assertEqual(self.run_backup('--force').returncode, 1)
        self.assertEqual((self.home / 'notification_calls').read_text().splitlines(), ['remove:999', 'remove:999'])
        self.assertEqual(self.reports()[-1]['status_code'], 2)

    def test_failed_delivery_retried_before_daily_lock(self):
        self.task('10 success.sh', 0)
        self.env['HTTP_STATUS'] = '503'
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(len(list((self.backup / '.monitoring-pending').glob('*.json'))), 1)
        self.env['HTTP_STATUS'] = '201'
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertEqual(len(self.reports()), 2)
        self.assertEqual(list((self.backup / '.monitoring-pending').glob('*.json')), [])

    def test_transport_error_is_retained(self):
        self.task('10 success.sh', 0)
        self.env.update(HTTP_STATUS='000', CURL_EXIT='7')
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(len(list((self.backup / '.monitoring-pending').glob('*.json'))), 1)

    def test_missing_tasks_is_error(self):
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['status_code'], 2)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['script'], 'tasks')

    def test_sync_error_is_not_hidden(self):
        self.task('10 success.sh', 0)
        self.env['SYNC_EXIT'] = '12'
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['exit_code'], 12)

    def test_successful_task_is_not_repeated_after_sibling_failure(self):
        self.task('10 success.sh', 0)
        self.task('20 failure.sh', 9)
        self.assertEqual(self.run_backup().returncode, 1)
        self.task('10 success.sh', 99)
        self.task('20 failure.sh', 0)
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertEqual(self.reports()[-1]['status_code'], 0)

    def test_worker_preserves_rclone_exit_code(self):
        shutil.copyfile(Path(__file__).parent / 'scripts/10_backup.sh', self.backup / '10_backup.sh')
        (self.backup / '.rclone-filters.conf').write_text('+ /**\n')
        self.stub('pgrep', '#!/bin/bash\nexit 1\n')
        self.stub('rclone', '#!/bin/bash\nif [[ "$*" == *"/storage/emulated/0"* ]]; then exit 23; fi\nexit 0\n')
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['exit_code'], 23)

    def test_second_runner_skips_while_first_is_executing_a_task(self):
        marker = self.home / 'task_started'
        release = self.home / 'release_task'
        self.task('10 wait.sh', 0)
        (self.backup / '10 wait.sh').write_text(
            '#!/bin/bash\n'
            'touch "$HOME/task_started"\n'
            'until [ -f "$HOME/release_task" ]; do sleep 0.05; done\n'
        )
        first = subprocess.Popen(
            ['bash', str(self.backup / 'run_backup.sh')], env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), 'Pierwszy runner nie rozpoczął zadania')
            self.assertIsNone(first.poll())
            self.assertEqual(self.run_backup().returncode, 0)
            self.assertEqual(self.reports(), [])
            self.assertIn(
                'Inna instancja run_backup.sh',
                (self.backup / 'logs/backup.log').read_text(),
            )
        finally:
            release.touch()
            first.wait(timeout=5)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(len(self.reports()), 1)
        self.assertEqual(self.run_backup('--force').returncode, 0)
        self.assertEqual(len(self.reports()), 2)

    def test_autoupdate_restarts_runner_without_losing_lock(self):
        self.task('10 success.sh', 0)
        self.stub('rclone', '#!/bin/bash\n'
                  'if [[ "$*" == *"/.config/backup/scripts"* ]] && [ ! -f "$HOME/updated" ]; then\n'
                  '  touch "$HOME/updated"\n'
                  '  printf "\\n# aktualizacja\\n" >> "$HOME/backup/run_backup.sh"\n'
                  'fi\n'
                  'exit 0\n')
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertTrue((self.home / 'updated').exists())
        self.assertEqual(len(self.reports()), 1)
        self.assertEqual((self.backup / 'run_backup.sh').stat().st_mode & 0o777, 0o700)

    def test_legacy_pi_files_are_copied_and_new_paths_are_used(self):
        pi_root = self.home / 'pi' / 'test'
        old_scripts = pi_root / '.skrypty'
        old_logs = pi_root / 'logs'
        old_scripts.mkdir(parents=True)
        old_logs.mkdir()
        shutil.copyfile(self.backup / 'run_backup.sh', old_scripts / 'run_backup.sh')
        (old_scripts / '10 from pi.sh').write_text('#!/bin/bash\nexit 0\n')
        (old_logs / 'history.log').write_text('stary wpis\n')
        (self.backup / '.user.conf').write_text(f'KTO="test"\nDEST_BASE="{self.home / "pi"}"\n')
        self.stub('ssh', '#!/bin/bash\ncommand="${@: -1}"\nbash -c "$command"\n')
        self.stub('rclone', '#!/bin/bash\n'
                  'while [ "$1" != copy ]; do shift; done\n'
                  'source="$2"; destination="$3"\n'
                  'if [[ "$source" == pi:* ]]; then\n'
                  '  cp -R "${source#pi:}/." "$destination/"\n'
                  'elif [[ "$destination" == pi:* ]]; then\n'
                  '  mkdir -p "${destination#pi:}"\n'
                  '  cp -R "$source/." "${destination#pi:}/"\n'
                  'fi\n')
        self.assertEqual(self.run_backup().returncode, 0)
        new_root = pi_root / '.config' / 'backup'
        self.assertTrue((new_root / 'scripts' / '10 from pi.sh').exists())
        self.assertEqual((new_root / 'scripts' / 'run_backup.sh').stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.backup / '10 from pi.sh').exists())
        self.assertEqual((self.backup / 'run_backup.sh').stat().st_mode & 0o777, 0o700)
        self.assertEqual((new_root / 'logs' / 'history.log').read_text(), 'stary wpis\n')
        self.assertTrue((new_root / 'logs' / 'backup.log').exists())
        self.assertTrue(old_scripts.exists())
        self.assertTrue(old_logs.exists())
        self.assertEqual(new_root.stat().st_mode & 0o777, 0o700)

    def test_remote_directory_error_is_reported_without_running_tasks(self):
        self.task('10 success.sh', 0)
        self.stub('ssh', '#!/bin/bash\n'
                  'if [ "${@: -1}" = true ]; then exit 0; fi\n'
                  'exit 13\n')
        self.assertEqual(self.run_backup().returncode, 1)
        self.assertEqual(self.reports()[0]['status_code'], 2)
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['script'], 'prepare_remote')
        self.assertEqual(self.reports()[0]['failed_tasks'][0]['exit_code'], 13)
        self.assertFalse((self.backup / '.locks/10 success.sh.done').exists())
