"""Testy rzeczywistego runnera z atrapami sieci i narzędzi Termux."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

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
        for tool in ('ssh', 'termux-wake-lock', 'termux-wake-unlock', 'termux-notification'):
            self.stub(tool, '#!/bin/bash\nexit 0\n')
        self.stub('rclone', '#!/bin/bash\nif [[ "$*" == *".skrypty"* ]]; then exit "${SYNC_EXIT:-0}"; fi\nexit 0\n')
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

    def run_backup(self):
        return subprocess.run(['bash', str(self.backup / 'run_backup.sh')], env=self.env, capture_output=True, timeout=10)

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
