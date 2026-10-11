import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
import requests

from monitoring.client import MonitoringReporter
from monitoring.models import LogEntry, MonitoredService
from .models import Dziecko, Ogloszenie, Wiadomosc
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


class DashboardTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username='rodzic')
        users = (
            User.objects.create_user(username='ala', first_name='Ala', last_name='Nowak'),
            User.objects.create_user(username='zuzia', first_name='Zuzia', last_name='Nowak'),
        )
        self.children = [
            Dziecko.objects.create(user=user, librus_login=f'login-{user.username}')
            for user in users
        ]

    def test_dashboard_and_grade_pages_require_login(self):
        for url in (reverse('librus:dashboard'), reverse('librus:grades', args=[self.children[0].pk])):
            response = self.client.get(url)
            self.assertRedirects(
                response, f'{reverse("backup_login")}?next={url}',
                fetch_redirect_response=False,
            )

    def test_recent_records_are_grouped_by_child_and_sorted_by_librus_date(self):
        now = timezone.now()
        for index in range(7):
            Wiadomosc.objects.create(
                wiadomosc_id=f'm-{index}', dziecko=self.children[0],
                librus_data=now - timedelta(days=index),
                temat=f'Wiadomość {index}', tresc='<script>alert(1)</script>',
            )
            Ogloszenie.objects.create(
                ogloszenie_id=f'a-{index}', dziecko=self.children[0],
                librus_data=now - timedelta(days=index),
                tytul=f'Ogłoszenie {index}', tresc='Treść ogłoszenia',
            )
        Wiadomosc.objects.create(
            wiadomosc_id='z-1', dziecko=self.children[1],
            temat='Wiadomość Zuzi', tresc='Dla Zuzi',
        )
        Ogloszenie.objects.create(
            ogloszenie_id='z-1', dziecko=self.children[1],
            tytul='Ogłoszenie Zuzi', tresc='Dla Zuzi',
        )
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('librus:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(len(response.context['children']), 2)
        ala, zuzia = response.context['children']
        self.assertEqual([msg.temat for msg in ala['messages']], [f'Wiadomość {i}' for i in range(5)])
        self.assertEqual([item.tytul for item in ala['announcements']], [f'Ogłoszenie {i}' for i in range(5)])
        self.assertEqual([msg.temat for msg in zuzia['messages']], ['Wiadomość Zuzi'])
        self.assertEqual([item.tytul for item in zuzia['announcements']], ['Ogłoszenie Zuzi'])
        self.assertContains(response, reverse('librus:grades', args=[self.children[0].pk]))
        self.assertContains(response, reverse('librus:grades', args=[self.children[1].pk]))
        self.assertNotContains(response, '<script>alert(1)</script>', html=False)

    def test_latest_fetch_comes_from_monitoring_report_and_shows_per_child_result(self):
        service = MonitoredService.objects.create(owner=self.viewer, service_name='librus - pobieranie danych')
        LogEntry.objects.create(service=service, status_code=0, message={
            'results': [{'dziecko': 'ala', 'status_code': 0}, {'dziecko': 'zuzia', 'status_code': 0}],
        })
        latest = LogEntry.objects.create(service=service, status_code=2, message={
            'results': [{'dziecko': 'ala', 'status_code': 0}, {'dziecko': 'zuzia', 'status_code': 2}],
        })
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('librus:dashboard'))
        self.assertEqual(response.context['latest_report'].pk, latest.pk)
        ala, zuzia = response.context['children']
        self.assertEqual(ala['sync_result']['status_code'], 0)
        self.assertEqual(zuzia['sync_result']['status_code'], 2)
        self.assertContains(response, 'Zgłoszono błąd')

    def test_dashboard_handles_report_without_child_results(self):
        service = MonitoredService.objects.create(owner=self.viewer, service_name='librus - pobieranie danych')
        LogEntry.objects.create(service=service, status_code=2, message={'results': None})
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('librus:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['children'][0]['sync_result'])

    def test_grade_page_is_a_placeholder_for_the_selected_child(self):
        self.client.force_login(self.viewer)
        url = reverse('librus:grades', args=[self.children[0].pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertContains(response, 'Oceny — Ala Nowak')
        self.assertNotContains(response, 'Zuzia Nowak')
        self.assertEqual(self.client.get(reverse('librus:grades', args=[99999])).status_code, 404)
