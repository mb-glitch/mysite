import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, override_settings
import requests

from monitoring.client import MonitoringReporter
from .management.commands.librus_sprawdz import Command, Librus


@override_settings(LIBRUS_MONITORING_TOKEN='test-token', URL_MONITORING='http://testserver/api/monitoring/')
class ReporterTests(SimpleTestCase):
    @patch('monitoring.client.requests.post')
    def test_token_payload_and_explicit_success(self, post):
        post.return_value.status_code = 201
        reporter = MonitoringReporter()
        message = {'results': [{'dziecko': 'test', 'status_code': 0}]}
        self.assertTrue(reporter.send(status_code=0, message=message))
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'http://testserver/api/monitoring/')
        self.assertEqual(kwargs['headers']['Authorization'], 'Token test-token')
        self.assertEqual(kwargs['json']['status_code'], 0)
        self.assertIn('execute_time_ms', kwargs['json'])
        self.assertEqual(kwargs['timeout'], 10)
        self.assertNotIn('status_code', message)

    @patch('monitoring.client.requests.post')
    def test_rejected_or_unreachable_endpoint(self, post):
        post.return_value.status_code = 403
        self.assertFalse(MonitoringReporter().send())
        post.side_effect = requests.ConnectionError('unreachable')
        self.assertFalse(MonitoringReporter().send())

    @override_settings(LIBRUS_MONITORING_TOKEN=None)
    @patch('monitoring.client.requests.post')
    def test_missing_token_does_not_send_request(self, post):
        self.assertFalse(MonitoringReporter().send())
        post.assert_not_called()


class LibrusMonitoringTests(SimpleTestCase):
    def run_command(self, client_side_effect=None, missing_credentials=False):
        env = {
            'LIBRUS_USER_ASIA': '' if missing_credentials else 'login1', 'LIBRUS_PASS_ASIA': 'password1', 'LIBRUS_IMIE_ASIA': 'asia',
            'LIBRUS_USER_ZUZIA': 'login2', 'LIBRUS_PASS_ZUZIA': 'password2', 'LIBRUS_IMIE_ZUZIA': 'zuzia',
        }
        module = 'librus.management.commands.librus_sprawdz'
        with patch.dict(os.environ, env), patch(module + '.Librus') as client, patch(module + '.Dziecko') as dziecko, patch(module + '.MonitoringReporter') as reporter:
            dziecko.objects.filter.return_value.first.return_value = SimpleNamespace()
            if client_side_effect:
                client.side_effect = client_side_effect
            command = Command()
            with patch.object(command, 'aktualizuj_baze'), patch.object(command, 'aktualizuj_ogloszenia'):
                command.handle()
            reporter.return_value.send.assert_called_once()
            return reporter.return_value.send.call_args.kwargs

    def test_success(self):
        report = self.run_command()
        self.assertEqual(report['status_code'], 0)
        self.assertEqual(len(report['message']['results']), 2)

    def test_login_failure_is_reported_even_without_client(self):
        report = self.run_command([RuntimeError('Login failed'), MagicMock()])
        self.assertEqual(report['status_code'], 2)
        self.assertEqual(report['message']['results'][1]['status_code'], 0)
        self.assertIn('asia: Login failed', report['message']['error'])

    def test_missing_credentials_is_not_silent_success(self):
        report = self.run_command(missing_credentials=True)
        self.assertEqual(report['status_code'], 2)
        self.assertIn('Brak danych logowania', report['message']['error'])

    def test_download_errors_propagate_to_report(self):
        client = Librus.__new__(Librus)
        client.session = MagicMock()
        client.session.get.side_effect = requests.Timeout('timeout')
        for method, args in ((client.sprawdz_wiadomosci, ()), (client.sprawdz_ogloszenia, ()), (client.pobierz_tresc, ('/test',))):
            with self.subTest(method=method.__name__), self.assertRaises(requests.Timeout):
                method(*args)
